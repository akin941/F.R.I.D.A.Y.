"""The owner-only, web-only `/skills` surface (Phase 12 D14, D20).

Default authentication (the trusted device's web session): a PAT, a node
secret or a node voice token is refused by authentication itself (401).
Permission classes refuse every other web principal (an agent, a member,
a non-owner: 403), and the services check the workspace owner over the
web again (`core.owner_web`). Workspaces, skills and versions are resolved
through `for_actor()`; another workspace's row is a 404.

- `GET  /api/v1/skills[?workspace=&status=]`
- `POST /api/v1/skills[?workspace=]` `{"text"}` -- owner-typed SKILL.md
- `POST /api/v1/skills/upload[?workspace=]` multipart `file` -- a SKILL.md
  or a .zip bundle (<= `SKILLS_BUNDLE_MAX_BYTES`, also on the body)
- `GET  /api/v1/skills/<id>` -- the skill and every version (full text)
- `POST /api/v1/skills/<id>/versions` `{"text"}` -- an edit = a new version
- `POST /api/v1/skills/<id>/archive`, `/restore`
- `POST /api/v1/skills/versions/<id>/approve` `{"sha256"}`
- `POST /api/v1/skills/versions/<id>/edit-approve` `{"sha256", "text"}`
- `POST /api/v1/skills/versions/<id>/reject` `{"sha256"?}`

There is no delete (a 405) and no bulk endpoint: one item at a time.
"""

import uuid

from django.conf import settings
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api_errors import service_error_response
from core.channels import request_channel
from core.permissions import IsOwner, IsWebChannel
from skills import selectors, services
from skills.models import InstructionSkill, InstructionSkillVersion
from skills.serializers import (
    STATUSES,
    DecisionSerializer,
    EditApproveSerializer,
    RejectSerializer,
    SkillTextSerializer,
    skill_detail,
    skill_summary,
    version_payload,
)
from workspaces.models import Workspace

_PERMISSIONS = [IsAuthenticated, IsWebChannel, IsOwner]
# Multipart framing around the file itself.
_BODY_OVERHEAD_BYTES = 64 * 1024


def _workspace(request):
    raw = request.query_params.get("workspace")
    workspaces = Workspace.objects.for_actor(request.user)
    if raw:
        try:
            uuid.UUID(str(raw))
        except ValueError:
            return None
        return workspaces.filter(pk=raw).first()
    return (
        workspaces.filter(type=Workspace.Type.PERSONAL, owner=request.user)
        .order_by("created_at")
        .first()
    )


def _not_found():
    return Response(status=status.HTTP_404_NOT_FOUND)


def _error(exc):
    if isinstance(exc, services.SkillPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    response = service_error_response(exc, status.HTTP_400_BAD_REQUEST)
    reason = getattr(exc, "reason", "")
    if reason:
        response.data["reason"] = reason
    return response


def _owns(request, workspace):
    return workspace is not None and str(workspace.owner_id) == str(request.user.pk)


class SkillListView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request):
        workspace = _workspace(request)
        if not _owns(request, workspace):
            return _not_found()
        rows = selectors.skills_for(request.user, workspace).annotate(
            pending=Count(
                "versions",
                filter=Q(versions__decision=InstructionSkillVersion.Decision.PENDING),
            )
        )
        wanted = request.query_params.get("status")
        if wanted:
            if wanted not in STATUSES:
                return Response(
                    {"detail": "Unknown status.", "code": "invalid"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            rows = rows.filter(status=wanted)
        return Response(
            {
                "workspace": str(workspace.pk),
                "limits": {
                    "max_active": int(settings.SKILLS_MAX_ACTIVE),
                    "body_max_chars": int(settings.SKILLS_BODY_MAX_CHARS),
                    "bundle_max_bytes": int(settings.SKILLS_BUNDLE_MAX_BYTES),
                },
                "skills": [
                    skill_summary(row, pending=row.pending) for row in rows.order_by("name")
                ],
            }
        )

    def post(self, request):
        workspace = _workspace(request)
        if workspace is None:
            return _not_found()
        serializer = SkillTextSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            parsed = services.parse_text(serializer.validated_data["text"])
            version = services.propose_from_owner(
                actor=request.user,
                channel=request_channel(request),
                workspace=workspace,
                parsed=parsed,
                origin=InstructionSkill.Origin.OWNER,
            )
        except (services.SkillServiceError, services.SkillPermissionDenied) as exc:
            return _error(exc)
        return Response(version_payload(version), status=status.HTTP_201_CREATED)


class SkillUploadView(APIView):
    permission_classes = _PERMISSIONS
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        max_bytes = int(settings.SKILLS_BUNDLE_MAX_BYTES)
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declared = 0
        if declared > max_bytes + _BODY_OVERHEAD_BYTES:
            # Refused before the body is parsed.
            return _error(services.SkillBundleTooLarge("The upload is too large."))
        workspace = _workspace(request)
        if workspace is None:
            return _not_found()
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"detail": "A 'file' is required.", "code": "invalid"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            if upload.size > max_bytes:
                raise services.SkillBundleTooLarge("The upload is too large.")
            data = upload.read(max_bytes + 1)
            parsed = services.parse_upload(data, filename=upload.name or "")
            version = services.propose_from_owner(
                actor=request.user,
                channel=request_channel(request),
                workspace=workspace,
                parsed=parsed,
                origin=InstructionSkill.Origin.UPLOAD,
            )
        except (services.SkillServiceError, services.SkillPermissionDenied) as exc:
            return _error(exc)
        return Response(version_payload(version), status=status.HTTP_201_CREATED)


class SkillDetailView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request, skill_id):
        skill = selectors.get_skill(request.user, skill_id)
        if skill is None or not _owns(request, skill.workspace):
            return _not_found()
        return Response(skill_detail(skill))


class SkillVersionCreateView(APIView):
    permission_classes = _PERMISSIONS
    parser_classes = [JSONParser]

    def post(self, request, skill_id):
        skill = selectors.get_skill(request.user, skill_id)
        if skill is None:
            return _not_found()
        serializer = SkillTextSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            parsed = services.parse_text(serializer.validated_data["text"])
            version = services.propose_from_owner(
                actor=request.user,
                channel=request_channel(request),
                workspace=skill.workspace,
                parsed=parsed,
                origin=skill.origin,
                skill=skill,
            )
        except (services.SkillServiceError, services.SkillPermissionDenied) as exc:
            return _error(exc)
        return Response(version_payload(version), status=status.HTTP_201_CREATED)


class _SkillActionView(APIView):
    permission_classes = _PERMISSIONS
    action = None

    def post(self, request, skill_id):
        skill = selectors.get_skill(request.user, skill_id)
        if skill is None:
            return _not_found()
        try:
            skill = type(self).action(
                actor=request.user, channel=request_channel(request), skill=skill
            )
        except (services.SkillServiceError, services.SkillPermissionDenied) as exc:
            return _error(exc)
        return Response(skill_detail(skill))


class SkillArchiveView(_SkillActionView):
    action = staticmethod(services.archive_skill)


class SkillRestoreView(_SkillActionView):
    action = staticmethod(services.restore_skill)


def _version(request, version_id):
    return selectors.get_version(request.user, version_id)


class VersionApproveView(APIView):
    permission_classes = _PERMISSIONS

    def post(self, request, version_id):
        version = _version(request, version_id)
        if version is None:
            return _not_found()
        serializer = DecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            version = services.approve_version(
                actor=request.user,
                channel=request_channel(request),
                version=version,
                expected_sha256=serializer.validated_data["sha256"],
            )
        except (services.SkillServiceError, services.SkillPermissionDenied) as exc:
            return _error(exc)
        return Response(version_payload(version))


class VersionEditApproveView(APIView):
    permission_classes = _PERMISSIONS

    def post(self, request, version_id):
        version = _version(request, version_id)
        if version is None:
            return _not_found()
        serializer = EditApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            version = services.edit_and_approve(
                actor=request.user,
                channel=request_channel(request),
                version=version,
                expected_sha256=serializer.validated_data["sha256"],
                text=serializer.validated_data["text"],
            )
        except (services.SkillServiceError, services.SkillPermissionDenied) as exc:
            return _error(exc)
        return Response(version_payload(version))


class VersionRejectView(APIView):
    permission_classes = _PERMISSIONS

    def post(self, request, version_id):
        version = _version(request, version_id)
        if version is None:
            return _not_found()
        serializer = RejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            version = services.reject_version(
                actor=request.user,
                channel=request_channel(request),
                version=version,
                expected_sha256=serializer.validated_data.get("sha256"),
            )
        except (services.SkillServiceError, services.SkillPermissionDenied) as exc:
            return _error(exc)
        return Response(version_payload(version))
