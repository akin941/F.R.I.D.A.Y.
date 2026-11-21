"""Node HTTP endpoints. Branch 2: the anonymous pairing endpoint (D4). The
owner-only node APIs (`GET /nodes`, detail, revoke, invocations) arrive in
branches 4 and 5."""

import logging

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import devices
from core.client_ip import trusted_client_ip
from core.throttling import ScopedRateThrottle
from nodes import services
from nodes.keys import SigningKeyUnavailable
from nodes.serializers import NodePairSerializer

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
