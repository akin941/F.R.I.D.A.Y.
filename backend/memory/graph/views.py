"""The owner graph API (Phase 8B branch 1, brief D3): `/api/v1/memory/graph/`.

The installation owner, a human over a trusted device's web session only
(`IsWebChannel` + `IsOwner`); default authentication only, so a CLI,
facade or hook PAT never reaches these views. Every lookup goes through
`for_actor()`: another workspace's entity or fact is 404, never 403. The
services re-check the owner role and the channel. A Graphify `system` row
through approve, edit, supersede or trash is 405. This is not a model path,
so the owner sees `private` facts here (`include_private=True`); reads are
audited with ids and counts only.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.services import record_audit
from core.api_errors import service_error_response
from core.channels import request_channel
from core.permissions import IsOwner, IsWebChannel
from memory.graph import selectors, services
from memory.graph.candidates import normalise_name
from memory.graph.serializers import (
    AsOfQuerySerializer,
    EntityListQuerySerializer,
    FactCreateSerializer,
    FactEditSerializer,
    FactListQuerySerializer,
    FactSupersedeSerializer,
    NeighbourhoodQuerySerializer,
    serialize_entity,
    serialize_fact,
)
from memory.models import MemoryEntity, MemoryFact
from workspaces.models import Workspace

_PERMISSIONS = [IsAuthenticated, IsWebChannel, IsOwner]
_LIST_CAP = 500


def _error(exc):
    if isinstance(exc, services.GraphPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    if isinstance(exc, services.SystemRowImmutable):
        return service_error_response(exc, status.HTTP_405_METHOD_NOT_ALLOWED)
    if isinstance(
        exc,
        (
            services.SupersedeOrder,
            services.SupersedeTargetChanged,
            services.GraphInvalidTransition,
        ),
    ):
        return service_error_response(exc, status.HTTP_409_CONFLICT)
    if isinstance(exc, services.GraphError | services.InvalidTransition):
        return service_error_response(exc, status.HTTP_400_BAD_REQUEST)
    raise exc


def _workspace(request, workspace_id):
    return Workspace.objects.for_actor(request.user, workspace=workspace_id).first()


def _entity(request, entity_id, *, manager=None):
    manager = manager or MemoryEntity.objects
    return manager.for_actor(request.user).filter(id=entity_id).select_related("workspace").first()


def _fact(request, fact_id, *, manager=None):
    manager = manager or MemoryFact.objects
    return (
        manager.for_actor(request.user)
        .filter(id=fact_id)
        .select_related("workspace", "subject", "object_entity")
        .first()
    )


def _viewed(request, workspace, view, **counts):
    record_audit(
        action="memory.graph.viewed",
        actor=request.user,
        workspace=workspace,
        metadata={"view": view, **counts},
    )


def _owner_read(request, workspace):
    services.require_owner_read(request.user, workspace, request_channel(request))


class EntityListView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request):
        query = EntityListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        workspace = _workspace(request, query.validated_data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            _owner_read(request, workspace)
        except services.MemoryServiceError as exc:
            return _error(exc)
        entities = MemoryEntity.objects.for_actor(request.user, workspace=workspace).with_privacy()
        if kind := query.validated_data.get("kind"):
            entities = entities.filter(kind=kind)
        if q := query.validated_data.get("q"):
            entities = entities.filter(normalised_name__contains=normalise_name(q))
        rows = list(entities.order_by("kind", "normalised_name")[:_LIST_CAP])
        _viewed(request, workspace, "entities", count=len(rows))
        return Response([serialize_entity(e) for e in rows])


class EntityDetailView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request, entity_id):
        entity = _entity(request, entity_id)
        if entity is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        query = NeighbourhoodQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            _owner_read(request, entity.workspace)
        except services.MemoryServiceError as exc:
            return _error(exc)
        edges = selectors.neighbourhood(
            [entity.pk],
            workspace=entity.workspace,
            hops=query.validated_data["hops"],
            as_of=query.validated_data.get("as_of"),
            include_system=True,
            include_private=True,
        )
        _viewed(request, entity.workspace, "entity", entity_id=str(entity.pk), edges=len(edges))
        return Response(
            {
                "entity": serialize_entity(entity, is_private=selectors.entity_is_private(entity)),
                "edges": [serialize_fact(f) for f in edges],
            }
        )

    def delete(self, request, entity_id):
        entity = _entity(request, entity_id)
        if entity is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            result = services.trash_entity(
                entity=entity, actor=request.user, channel=request_channel(request)
            )
        except services.MemoryServiceError as exc:
            return _error(exc)
        return Response(result)


class EntityRestoreView(APIView):
    permission_classes = _PERMISSIONS

    def post(self, request, entity_id):
        entity = _entity(request, entity_id, manager=MemoryEntity.all_objects)
        if entity is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            result = services.restore_entity(
                entity=entity, actor=request.user, channel=request_channel(request)
            )
        except services.MemoryServiceError as exc:
            return _error(exc)
        return Response(result)


class EntityFactsView(APIView):
    """The entity's timeline: every approved/system version (superseded
    history included) and pending proposals, oldest first; or, with
    `as_of`, only the facts valid then."""

    permission_classes = _PERMISSIONS

    def get(self, request, entity_id):
        entity = _entity(request, entity_id)
        if entity is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        query = AsOfQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            _owner_read(request, entity.workspace)
        except services.MemoryServiceError as exc:
            return _error(exc)
        as_of = query.validated_data.get("as_of")
        timeline = selectors.entity_timeline(entity, include_private=True, include_pending=True)
        if as_of is not None:
            timeline = selectors.facts_as_of(timeline, as_of)
        rows = list(timeline[:_LIST_CAP])
        _viewed(
            request, entity.workspace, "entity_facts", entity_id=str(entity.pk), count=len(rows)
        )
        return Response([serialize_fact(f) for f in rows])


class FactListCreateView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request):
        query = FactListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        workspace = _workspace(request, query.validated_data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            _owner_read(request, workspace)
        except services.MemoryServiceError as exc:
            return _error(exc)
        facts = MemoryFact.objects.for_actor(request.user, workspace=workspace)
        if fact_status := query.validated_data.get("status"):
            facts = facts.filter(status=fact_status)
        if query.validated_data.get("current"):
            facts = selectors.current_facts(facts)
        if (as_of := query.validated_data.get("as_of")) is not None:
            facts = selectors.facts_as_of(facts, as_of)
        rows = list(facts.select_related("subject", "object_entity")[:_LIST_CAP])
        _viewed(request, workspace, "facts", count=len(rows))
        return Response([serialize_fact(f) for f in rows])

    def post(self, request):
        body = FactCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        workspace = _workspace(request, data["workspace"])
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            fact = services.create_fact(
                workspace=workspace,
                actor=request.user,
                channel=request_channel(request),
                subject_kind=data["subject_kind"],
                subject_name=data["subject_name"],
                predicate=data["predicate"],
                valid_from=data.get("valid_from"),
                object_kind=data.get("object_kind", ""),
                object_name=data.get("object_name", ""),
                object_text=data.get("object_text", ""),
                statement=data.get("statement", ""),
                sensitivity=data["sensitivity"],
            )
        except services.MemoryServiceError as exc:
            return _error(exc)
        return Response(serialize_fact(fact), status=status.HTTP_201_CREATED)


class FactDetailView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request, fact_id):
        fact = _fact(request, fact_id)
        if fact is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            _owner_read(request, fact.workspace)
        except services.MemoryServiceError as exc:
            return _error(exc)
        _viewed(request, fact.workspace, "fact", fact_id=str(fact.pk))
        return Response(serialize_fact(fact))

    def patch(self, request, fact_id):
        fact = _fact(request, fact_id)
        if fact is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        body = FactEditSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            version = services.edit_fact(
                fact=fact,
                actor=request.user,
                channel=request_channel(request),
                **body.validated_data,
            )
        except services.MemoryServiceError as exc:
            return _error(exc)
        return Response(serialize_fact(version))

    def delete(self, request, fact_id):
        fact = _fact(request, fact_id)
        if fact is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            services.trash_fact(fact=fact, actor=request.user, channel=request_channel(request))
        except services.MemoryServiceError as exc:
            return _error(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class FactActionView(APIView):
    """`approve`, `reject`, `supersede` (the owner replaces a current fact
    from a date) and `restore` (out of the owner trash)."""

    permission_classes = _PERMISSIONS
    ACTIONS = ("approve", "reject", "supersede", "restore")

    def post(self, request, fact_id, action):
        if action not in self.ACTIONS:
            return Response(status=status.HTTP_404_NOT_FOUND)
        manager = MemoryFact.all_objects if action == "restore" else None
        fact = _fact(request, fact_id, manager=manager)
        if fact is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        channel = request_channel(request)
        try:
            if action == "approve":
                result = services.approve_fact(fact=fact, actor=request.user, channel=channel)
            elif action == "reject":
                result = services.reject_fact(fact=fact, actor=request.user, channel=channel)
            elif action == "restore":
                result = services.restore_fact(fact=fact, actor=request.user, channel=channel)
            else:
                body = FactSupersedeSerializer(data=request.data)
                body.is_valid(raise_exception=True)
                result = services.supersede_fact(
                    fact=fact, actor=request.user, channel=channel, **body.validated_data
                )
        except services.MemoryServiceError as exc:
            return _error(exc)
        result = MemoryFact.all_objects.select_related("subject", "object_entity").get(pk=result.pk)
        return Response(serialize_fact(result))
