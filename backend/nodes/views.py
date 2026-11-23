"""Node HTTP endpoints. Branch 2: the anonymous pairing endpoint (D4).
Branch 4: the owner-only node APIs `GET /api/v1/nodes`, `GET
/api/v1/nodes/<id>` and `POST /api/v1/nodes/<id>/revoke` (D11, backend
only). Branch 5 adds `GET /api/v1/nodes/<id>/invocations` (chain rows).

Phase 10 branch 3 (brief D8): `POST /api/v1/nodes/<node_id>/actions` (the
confirm-class path, the only caller of `nodes.services.invoke_confirm`) and
`GET /api/v1/nodes/<node_id>/actions/<execution_id>` (status only, never
output). Both are the node owner's web session only."""

import logging

from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import devices
from audit.services import record_audit
from core.client_ip import trusted_client_ip
from core.permissions import IsOwner, IsWebChannel
from core.throttling import ScopedRateThrottle
from nodes import selectors, services
from nodes.keys import SigningKeyUnavailable
from nodes.models import NodeActionExecution
from nodes.serializers import (
    NodeActionCreateSerializer,
    NodeActionExecutionSerializer,
    NodePairSerializer,
)

logger = logging.getLogger(__name__)

_USER_AGENT_MAX = 255


def _refusal(detail, code, http_status):
    return Response({"detail": detail, "code": code}, status=http_status)


class NodePairView(APIView):
    """`POST /api/v1/nodes/pair` -- a Friday Node pairs with a node pairing
    code (`manage.py issue_pairing_code --node`, or "Mac düğümü eşle" on
    `/devices`). Anonymous by design (the code is the credential), with the
    fail-closed `node_pair` throttle (a Redis outage is 503, never open).

    Returns exactly once: `node_id`, `node_secret` (`jnd_...`), the server
    signing key(s) to pin, `protocol_version`, `gateway_path` and
    `rotate_after_days`. The body is never logged: only the outcome and the
    node id are.

    Refusals: an invalid, expired, used or browser code is one generic 400
    (`invalid_code`; the reason is in the audit log only), a PIN from
    outside the pairing networks is 403, an engaged kill switch is 409, no
    common protocol version is 400 and a missing or invalid server signing
    key is 503 -- the last two before any code is consumed."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "node_pair"

    def post(self, request):
        serializer = NodePairSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = services.pair_node(
                raw_code=data["code"],
                node_name=data["node_name"],
                protocol_versions=data["protocol_versions"],
                client_ip=trusted_client_ip(request),
                user_agent=request.headers.get("User-Agent", "")[:_USER_AGENT_MAX],
            )
        except SigningKeyUnavailable as exc:
            logger.warning("node pairing refused: signing key unavailable (%s)", exc.reason)
            return _refusal(
                "Node pairing is unavailable: the server signing key is not configured.",
                "signing_unavailable",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except services.UnsupportedProtocolVersion:
            return _refusal(
                "No supported protocol version offered.",
                "unsupported_protocol_version",
                status.HTTP_400_BAD_REQUEST,
            )
        except devices.PinNetworkRefused:
            return _refusal(
                "PIN codes are accepted only from the local network; use a strong code.",
                "pin_network_refused",
                status.HTTP_403_FORBIDDEN,
            )
        except devices.NodeKillSwitchEngaged:
            return _refusal(
                "The owner's node kill switch is engaged.",
                "kill_switch_engaged",
                status.HTTP_409_CONFLICT,
            )
        except (devices.InvalidPairingCode, devices.OwnerNotConfigured):
            return _refusal("Invalid or expired code.", "invalid_code", status.HTTP_400_BAD_REQUEST)
        logger.info("node paired node_id=%s", result.node.pk)
        return Response(result.response_payload(), status=status.HTTP_201_CREATED)


class IsHumanActor(BasePermission):
    """No machine (`is_agent`) actor ever invokes a node action."""

    message = "Agent accounts cannot invoke node actions."
    code = "agent_forbidden"

    def has_permission(self, request, view):
        return not getattr(request.user, "is_agent", False)


_NOT_FOUND = {"detail": "Not found.", "code": "not_found"}


def _execution_body(execution):
    return NodeActionExecutionSerializer(execution).data


class NodeActionListView(APIView):
    """`POST /api/v1/nodes/<node_id>/actions` -- a confirm-class node action
    (`apps.open` / `apps.focus`, Phase 10 D8): the owner's card click on
    screen, confirmed later by the click in the node's own window. No
    `ApprovalRequest`. Body `{capability, args, source_message_id}`.

    Web channel, human, the node's owner (and an `owner` of its personal
    workspace) only. An unknown node and another user's node answer the same
    404 (`nodes.services.invoke_confirm` writes the `nodes.refused` audit
    row, and the chain row for a node that exists). A capability that is
    not confirm-class, or args outside the server contract, is a 400 with
    nothing written. Otherwise 202 with the execution (its status may
    already be terminal: offline, not invocable, kill switch...)."""

    permission_classes = [IsAuthenticated, IsWebChannel, IsHumanActor]

    def post(self, request, node_id):
        serializer = NodeActionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            execution = services.invoke_confirm(
                actor=request.user,
                node_id=node_id,
                capability=data["capability"],
                args=data["args"],
                source_message_id=data.get("source_message_id", ""),
            )
        except services.NodeNotFound:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        except services.ActionRefused as exc:
            return _refusal("This node action is not allowed.", exc.code, 400)
        return Response(_execution_body(execution), status=status.HTTP_202_ACCEPTED)


class NodeActionDetailView(APIView):
    """`GET /api/v1/nodes/<node_id>/actions/<execution_id>` -- one execution's
    status, exit code and confirmation state (never args or output), for
    the node's owner over the web channel; `for_actor()` scoped, 404
    otherwise."""

    permission_classes = [IsAuthenticated, IsWebChannel, IsHumanActor]

    def get(self, request, node_id, execution_id):
        execution = (
            NodeActionExecution.objects.for_actor(request.user)
            .filter(pk=execution_id, node_id=node_id, node__owner=request.user)
            .first()
        )
        if execution is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response(_execution_body(execution))


# --- the owner's node APIs (Phase 9 D11, branch 4) ---

_NODE_PERMISSIONS = [IsAuthenticated, IsWebChannel, IsOwner]


def _owned_node(request, node_id):
    """The actor's own node through `for_actor()`, or None (one 404 for an
    unknown node and another user's)."""
    return selectors.nodes_for(request.user).filter(pk=node_id).first()


class NodeListView(APIView):
    """`GET /api/v1/nodes` -- the owner's paired nodes with their link
    status, heartbeat, spool depth, credential age and catalogue counts.
    Owner, web channel, human only (default authentication: a node secret or
    a PAT never reaches it). Codes and counts only -- no path, no content."""

    permission_classes = _NODE_PERMISSIONS

    def get(self, request):
        nodes = selectors.nodes_for(request.user)
        return Response([selectors.node_summary(node) for node in nodes])


class NodeDetailView(APIView):
    """`GET /api/v1/nodes/<id>` -- one node plus its latest snapshot's
    entries (name, version, class, availability, invocable, and the
    `not_invocable` reason). Read-only: there is no policy surface (ADR-013
    decision 5), availability is what the node reported."""

    permission_classes = _NODE_PERMISSIONS

    def get(self, request, node_id):
        node = _owned_node(request, node_id)
        if node is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response(selectors.node_detail(node))


INVOCATIONS_PAGE_SIZE = 50


class NodeInvocationListView(APIView):
    """`GET /api/v1/nodes/<id>/invocations?cursor=<seq>` -- the node's last
    50 chain rows (D8, D11) in workspaces the actor can see (`for_actor()`),
    newest first; `cursor` is the `next_cursor` of the previous page (the
    rows with a lower `seq`). Each row: capability, status and refusal code,
    args and output hashes, sizes, the dropped-row count, duration, channel,
    trace id and the workspace's name -- never args or output content.
    Owner, web channel, human only. Audited `nodes.invocations.viewed`
    (counts only)."""

    permission_classes = _NODE_PERMISSIONS

    def get(self, request, node_id):
        node = _owned_node(request, node_id)
        if node is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        cursor = request.query_params.get("cursor")
        if cursor not in (None, ""):
            try:
                cursor = int(cursor)
            except (TypeError, ValueError):
                return _refusal("Invalid cursor.", "invalid_cursor", status.HTTP_400_BAD_REQUEST)
            if cursor < 0:
                return _refusal("Invalid cursor.", "invalid_cursor", status.HTTP_400_BAD_REQUEST)
        else:
            cursor = None
        rows = selectors.invocation_page(
            request.user, node, before_seq=cursor, limit=INVOCATIONS_PAGE_SIZE
        )
        next_cursor = rows[-1]["seq"] if len(rows) == INVOCATIONS_PAGE_SIZE else None
        record_audit(
            action="nodes.invocations.viewed",
            actor=request.user,
            workspace=node.workspace,
            resource=node,
            metadata={"node_id": str(node.pk), "count": len(rows), "cursor": cursor},
        )
        return Response({"results": rows, "next_cursor": next_cursor})


class NodeRevokeView(APIView):
    """`POST /api/v1/nodes/<id>/revoke` -- delegates to
    `accounts.devices.revoke_device` (permanent; audited
    `accounts.device.revoked`). The revocation is published after commit,
    so the gateway closes the node's socket within ~2 s (D6); the next
    heartbeat re-check is the backstop."""

    permission_classes = _NODE_PERMISSIONS

    def post(self, request, node_id):
        node = _owned_node(request, node_id)
        if node is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        devices.revoke_device(device=node.device, actor=request.user)
        node.device.refresh_from_db()
        return Response(selectors.node_summary(node))
