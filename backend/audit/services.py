"""Audit recording service — the only sanctioned way to write audit rows."""

from audit.models import AuditLog
from core.context import get_request_id

# Keys that must never appear in audit metadata (defence in depth; callers are
# responsible for not passing secrets in the first place).
_FORBIDDEN_METADATA_KEYS = {"password", "token", "access", "refresh", "api_key", "secret"}


def record_audit(
    *,
    action,
    actor=None,
    actor_type=AuditLog.ActorType.HUMAN,
    workspace=None,
    resource=None,
    metadata=None,
    request_id=None,
):
    """Create an immutable audit entry.

    `resource` may be a model instance — its label and pk are extracted.
    `request_id` defaults to the active request's id (RequestIDMiddleware).
    """
    metadata = dict(metadata or {})
    for key in list(metadata):
        if key.lower() in _FORBIDDEN_METADATA_KEYS:
            raise ValueError(f"Secret-like key {key!r} is not allowed in audit metadata")

    resource_type = ""
    resource_id = ""
    if resource is not None:
        resource_type = resource._meta.label_lower
        resource_id = str(resource.pk)

    # AnonymousUser → None; real users pass through.
    if actor is not None and not getattr(actor, "is_authenticated", True):
        actor = None

    return AuditLog.objects.create(
        action=action,
        actor=actor,
        actor_type=actor_type,
        workspace=workspace,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id or get_request_id() or "",
        metadata=metadata,
    )
