"""The owner's Home Assistant entity allowlist (Phase 13 D11).

`connectors.HomeEntity` rows are created, changed and removed **only** here,
by the installation owner over the web (`core.owner_web.require_owner_web`),
in the owner's **personal** workspace. Nothing else writes them: no tool,
wake route, schedule, probe or connector path (CLAUDE.md rule 4, an agent
can never widen what it may call; `connectors/tests/test_ha_allowlist_gate`
walks every registry to pin it). Adding an entity is the owner's act -- the
editor offers candidates from a live `ha_states` read, but nothing is ever
auto-applied.

Audited `ha.entity_allowed` / `ha.entity_changed` / `ha.entity_removed` with
ids, keys, the domain and service names only (no HA-supplied text).

Rules:
- `entity_id` matches the bridge's `ENTITY_RE` and its domain is in the
  code maximum (never a `NEVER_DOMAINS`, read-only or Wake-on-LAN domain)
  and not narrowed away by `HA_ALLOWED_DOMAINS` (`ha_entity.not_allowed`);
- `services` is a non-empty subset of `SERVICES[domain]` with no repeats
  (`ha_entity.not_allowed`); `HomeEntity.save` re-checks the code maximum;
- `label` / `aliases` follow the shared label rules
  (`workspaces.rooms.clean_label` / `clean_aliases`: `label.*` codes); an
  alias may not repeat the entity's own label; two entities may share a
  label (the home routes ask when a name is ambiguous);
- `room_id` is a room of the same workspace (`label.unknown_room`);
- one row per `entity_id` per workspace (`ha_entity.conflict`, 409),
  checked under a lock on the workspace row.
"""

from django.db import IntegrityError, transaction

from audit.services import record_audit
from connectors.bridges import home_assistant as bridge
from connectors.models import HOME_ENTITY_LABEL_MAX_CHARS, HomeEntity
from connectors.services import ConnectorPermissionDenied, ConnectorServiceError
from core.owner_web import OwnerWebRequired, require_owner_web
from workspaces.models import Room, Workspace
from workspaces.rooms import LabelConflict, LabelUnknownRoom, clean_aliases, clean_label

UNSET = object()


class HomeEntityError(ConnectorServiceError):
    """A refused allowlist write; `http_status` is what the views answer."""

    code = "ha_entity.invalid"
    http_status = 400


class HomeEntityInvalid(HomeEntityError):
    """Not an entity id (`ENTITY_RE`), or a malformed field."""

    code = "ha_entity.invalid"


class HomeEntityNotAllowed(HomeEntityError):
    """A domain or service outside the code maximum (or narrowed away by
    `HA_ALLOWED_DOMAINS`), a `NEVER_DOMAINS` / read-only entity, or no
    service at all."""

    code = "ha_entity.not_allowed"


class HomeEntityConflict(HomeEntityError):
    """The entity is already on the allowlist."""

    code = "ha_entity.conflict"
    http_status = 409


class HomeEntityReadFailed(HomeEntityError):
    """The live `ha_states` read for the editor's candidates failed; the
    message carries only the read's closed error code."""

    code = "ha_entity.read_failed"
    http_status = 502


class HomeEntityPermissionDenied(ConnectorPermissionDenied):
    """Not the owner over the web, or not the personal workspace (403)."""


def require_entities_owner_web(*, actor, workspace, channel):
    try:
        require_owner_web(actor=actor, workspace=workspace, channel=channel)
    except OwnerWebRequired as exc:
        raise HomeEntityPermissionDenied(str(exc)) from exc
    if workspace.type != Workspace.Type.PERSONAL:
        # The views answer 404 before this; a direct caller is refused too.
        raise HomeEntityPermissionDenied("Home entities belong to the personal workspace.")


def _check_entity(entity_id):
    if not isinstance(entity_id, str) or bridge.entity_domain(entity_id) is None:
        raise HomeEntityInvalid("Not a Home Assistant entity id.")
    domain = bridge.entity_domain(entity_id)
    if domain not in bridge.entity_domains() or domain not in bridge.service_domains():
        raise HomeEntityNotAllowed("This entity's domain can never be allowlisted here.")
    return domain


def _check_services(domain, services):
    if not isinstance(services, list | tuple):
        raise HomeEntityInvalid("Services must be a list.")
    services = list(services)
    allowed = set(bridge.allowed_services(domain))
    if (
        not services
        or len(set(services)) != len(services)
        or not all(isinstance(s, str) and s in allowed for s in services)
    ):
        raise HomeEntityNotAllowed("A service is outside this domain's code maximum.")
    # Stored in the code maximum's order, so equal sets compare equal.
    return [s for s in bridge.allowed_services(domain) if s in services]


def _room(workspace, room_id):
    if room_id is None:
        return None
    room = Room.objects.filter(workspace=workspace, pk=room_id).first()
    if room is None:
        raise LabelUnknownRoom("Not a room of this workspace.")
    return room


def _labels(label, aliases):
    text, key = clean_label(label, max_chars=HOME_ENTITY_LABEL_MAX_CHARS)
    alias_texts, alias_keys = clean_aliases(aliases)
    if key in alias_keys:
        raise LabelConflict("An alias repeats the entity's label.")
    return text, key, alias_texts


def _lock_workspace(workspace):
    return Workspace.objects.select_for_update().get(pk=workspace.pk)


def _audit(action, *, actor, entity, metadata=None):
    record_audit(
        action=action,
        actor=actor,
        workspace=entity.workspace,
        resource=entity,
        metadata={
            "home_entity_id": str(entity.pk),
            "entity_id": entity.entity_id,
            "domain": entity.domain,
            **(metadata or {}),
        },
    )


def list_entities(*, actor, workspace):
    """The workspace's allowlist through `for_actor()`."""
    return list(
        HomeEntity.objects.for_actor(actor, workspace=workspace)
        .select_related("room")
        .order_by("entity_id")
    )


def code_maximum():
    """`{domain: [service, ...]}` the editor may offer now: the code maximum
    narrowed by `HA_ALLOWED_DOMAINS` (never widened)."""
    return {
        domain: bridge.allowed_services(domain)
        for domain in sorted(bridge.entity_domains())
        if bridge.allowed_services(domain)
    }


@transaction.atomic
def create_entity(
    *,
    actor,
    workspace,
    channel,
    entity_id,
    label,
    services,
    room_id=None,
    aliases=None,
    enabled=True,
):
    """Allowlist one entity (owner, web only). Audited `ha.entity_allowed`."""
    require_entities_owner_web(actor=actor, workspace=workspace, channel=channel)
    domain = _check_entity(entity_id)
    clean_services = _check_services(domain, services)
    text, key, alias_texts = _labels(label, aliases)
    locked = _lock_workspace(workspace)
    room = _room(locked, room_id)
    if HomeEntity.objects.filter(workspace=locked, entity_id=entity_id).exists():
        raise HomeEntityConflict("This entity is already allowlisted.")
    try:
        with transaction.atomic():
            entity = HomeEntity.objects.create(
                workspace=locked,
                entity_id=entity_id,
                room=room,
                label=text,
                key=key,
                aliases=alias_texts,
                services=clean_services,
                enabled=bool(enabled),
            )
    except IntegrityError as exc:
        raise HomeEntityConflict("This entity is already allowlisted.") from exc
    _audit(
        "ha.entity_allowed",
        actor=actor,
        entity=entity,
        metadata={
            "key": key,
            "services": clean_services,
            "room_id": str(room.pk) if room else None,
            "alias_count": len(alias_texts),
            "enabled": entity.enabled,
        },
    )
    return entity


@transaction.atomic
def update_entity(
    *,
    actor,
    entity,
    channel,
    label=UNSET,
    room_id=UNSET,
    aliases=UNSET,
    services=UNSET,
    enabled=UNSET,
):
    """Relabel, move, re-alias, change the services of or switch one
    allowlisted entity on/off (owner, web only). `entity_id` never changes
    (remove and re-add instead). Audited `ha.entity_changed` with the
    changed field names; a no-op writes nothing. Narrowing (a service
    unticked, `enabled=False`) takes effect on pending approvals too: their
    execution precondition refuses the send (`connector_changed`)."""
    require_entities_owner_web(actor=actor, workspace=entity.workspace, channel=channel)
    locked_ws = _lock_workspace(entity.workspace)
    locked = HomeEntity.objects.select_for_update().get(pk=entity.pk, workspace=locked_ws)
    new_label, new_key = locked.label, locked.key
    new_aliases = list(locked.aliases or [])
    if label is not UNSET or aliases is not UNSET:
        new_label, new_key, new_aliases = _labels(
            locked.label if label is UNSET else label,
            new_aliases if aliases is UNSET else aliases,
        )
    changed = []
    if (new_label, new_key) != (locked.label, locked.key):
        locked.label, locked.key = new_label, new_key
        changed += ["label", "key"]
    if new_aliases != list(locked.aliases or []):
        locked.aliases = new_aliases
        changed.append("aliases")
    if room_id is not UNSET:
        room = _room(locked_ws, room_id)
        if (room.pk if room else None) != locked.room_id:
            locked.room = room
            changed.append("room")
    if services is not UNSET:
        clean_services = _check_services(locked.domain, services)
        if clean_services != list(locked.services or []):
            locked.services = clean_services
            changed.append("services")
    if enabled is not UNSET and bool(enabled) != locked.enabled:
        locked.enabled = bool(enabled)
        changed.append("enabled")
    if not changed:
        return locked
    locked.save(update_fields=[*changed, "updated_at"])
    _audit(
        "ha.entity_changed",
        actor=actor,
        entity=locked,
        metadata={
            "fields": sorted(set(changed) - {"key"}),
            "key": locked.key,
            "services": list(locked.services),
            "room_id": str(locked.room_id) if locked.room_id else None,
            "enabled": locked.enabled,
        },
    )
    return locked


def delete_entity(*, actor, entity, channel):
    """Remove one entity from the allowlist (owner, web only): nothing can
    call it any more, pending approvals included (`connector_changed` at
    execution). Audited `ha.entity_removed`."""
    require_entities_owner_web(actor=actor, workspace=entity.workspace, channel=channel)
    with transaction.atomic():
        locked = HomeEntity.objects.select_for_update().get(pk=entity.pk)
        metadata = {
            "home_entity_id": str(locked.pk),
            "entity_id": locked.entity_id,
            "domain": locked.domain,
            "key": locked.key,
        }
        workspace = locked.workspace
        locked.delete()
        record_audit(
            action="ha.entity_removed", actor=actor, workspace=workspace, metadata=metadata
        )


def entity_candidates(*, actor, workspace, channel):
    """The editor's pick list: one live `ha_states` read (the normal audited
    connector read: enabled binding, pin `ok`, web channel), reduced to the
    entities whose domain can be allowlisted now, each with the services the
    editor may offer and whether it is already on the allowlist. HA text
    (`friendly_name`, `source_list`) is display-only, bounded and stripped by
    the bridge; nothing here writes a row."""
    from connectors.manifest import get_manifest
    from connectors.reads import perform_read
    from tools.registry import ReadToolError

    require_entities_owner_web(actor=actor, workspace=workspace, channel=channel)
    manifest = get_manifest("home_assistant")
    policy = manifest.get_tool("ha_states") if manifest is not None else None
    if policy is None:
        raise HomeEntityReadFailed("not_available")
    try:
        result = perform_read(
            manifest,
            policy,
            workspace=workspace,
            actor=actor,
            args={},
            channel=channel,
            as_data=True,
        )
    except ReadToolError as exc:
        raise HomeEntityReadFailed(exc.code) from None
    data = result.structured if isinstance(result.structured, dict) else {}
    allowlisted = set(
        HomeEntity.objects.for_actor(actor, workspace=workspace).values_list("entity_id", flat=True)
    )
    candidates = []
    for state in data.get("entities") or []:
        entity_id = state.get("entity_id")
        domain = bridge.entity_domain(entity_id)
        services = bridge.allowed_services(domain)
        if not services:
            continue
        attributes = state.get("attributes") or {}
        candidates.append(
            {
                "entity_id": entity_id,
                "domain": domain,
                "friendly_name": attributes.get("friendly_name", ""),
                "state": state.get("state", ""),
                "source_list": attributes.get("source_list") or [],
                "services": services,
                "allowlisted": entity_id in allowlisted,
            }
        )
    return {"results": candidates, "omitted": data.get("omitted", 0)}
