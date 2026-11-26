"""Rooms and owner-set labels (Phase 13 D2, D3).

A room is a row of the owner's **personal** workspace. It is created,
renamed, re-aliased, re-ordered and deleted only by the installation owner
over the web (`core.owner_web.require_owner_web`), audited
`home.room_created` / `home.room_renamed` / `home.room_changed` /
`home.room_deleted` with ids and keys only.

`clean_label` / `clean_aliases` are the shared label rules `nodes` uses for
node names and aliases too (`workspaces` is the one kernel app both `nodes`
and `connectors` may import): a label is trimmed, bounded and keyed by
`core.speech.phrase_key`; aliases are at most `MAX_LABEL_ALIASES`, each
`LABEL_ALIAS_MIN_CHARS`-`LABEL_ALIAS_MAX_CHARS` characters, with distinct
keys.

Uniqueness: a room's name key and every alias key are unique across the
workspace's rooms, checked under a lock on the workspace row (the
`unique_room_key` constraint backs the name key).
"""

from django.db import transaction
from django.db.models import ProtectedError

from audit.services import record_audit
from core.owner_web import OwnerWebRequired, require_owner_web
from core.speech import phrase_key
from workspaces.models import (
    LABEL_ALIAS_MAX_CHARS,
    LABEL_ALIAS_MIN_CHARS,
    MAX_LABEL_ALIASES,
    ROOM_NAME_MAX_CHARS,
    Room,
    Workspace,
)


class LabelError(Exception):
    """A refused room / label write; each subclass carries its closed
    `code` (`core.error_codes`) and the HTTP status the views answer."""

    code = "label.invalid"
    http_status = 400


class LabelInvalid(LabelError):
    """A name or alias outside its length bounds, not text, or one that
    normalizes to nothing."""

    code = "label.invalid"


class LabelLimit(LabelError):
    """More aliases than `MAX_LABEL_ALIASES`."""

    code = "label.limit"


class LabelConflict(LabelError):
    """The name or an alias collides with another label of the same kind
    (or with another alias or the name of this one)."""

    code = "label.conflict"
    http_status = 409


class LabelUnknownRoom(LabelError):
    """A `room_id` that is not a room of the node's workspace."""

    code = "label.unknown_room"


class RoomInUse(LabelError):
    """A room some node (or, from Phase 13 branch 5, a home entity) still
    points at."""

    code = "room.in_use"
    http_status = 409


class RoomsPermissionDenied(Exception):
    """Not the owner over the web (the generic `core.owner_web` codes)."""

    def __init__(self, message, code="owner_required"):
        self.code = code
        super().__init__(message)


def clean_label(value, *, max_chars, min_chars=1):
    """`(text, key)`: the trimmed label and its `phrase_key`, or
    LabelInvalid."""
    if not isinstance(value, str):
        raise LabelInvalid("A label must be text.")
    text = " ".join(value.split())
    if not (min_chars <= len(text) <= max_chars):
        raise LabelInvalid(f"A label must be {min_chars}-{max_chars} characters.")
    key = phrase_key(text)
    if not key:
        raise LabelInvalid("A label must contain a letter or a digit.")
    return text, key


def clean_aliases(values):
    """`(aliases, keys)` in the given order, or a LabelError."""
    if values is None:
        values = []
    if not isinstance(values, list | tuple):
        raise LabelInvalid("Aliases must be a list.")
    if len(values) > MAX_LABEL_ALIASES:
        raise LabelLimit(f"At most {MAX_LABEL_ALIASES} aliases.")
    aliases, keys = [], []
    for value in values:
        text, key = clean_label(
            value, min_chars=LABEL_ALIAS_MIN_CHARS, max_chars=LABEL_ALIAS_MAX_CHARS
        )
        if key in keys:
            raise LabelConflict("Two aliases are the same.")
        aliases.append(text)
        keys.append(key)
    return aliases, keys


def label_keys(name_key, aliases) -> set:
    """Every key a label answers to: its name key and its alias keys."""
    return {name_key, *(phrase_key(alias) for alias in aliases or ())} - {""}


def require_rooms_owner_web(*, actor, workspace, channel):
    try:
        require_owner_web(actor=actor, workspace=workspace, channel=channel)
    except OwnerWebRequired as exc:
        raise RoomsPermissionDenied(str(exc), code=exc.code) from exc
    if workspace.type != Workspace.Type.PERSONAL:
        # The views answer 404 before this; a direct caller is refused too.
        raise RoomsPermissionDenied("Rooms belong to the personal workspace.")


def _lock_workspace(workspace):
    return Workspace.objects.select_for_update().get(pk=workspace.pk)


def _check_room_keys(workspace, keys, *, exclude_pk=None):
    taken = set()
    for other in Room.objects.filter(workspace=workspace).exclude(pk=exclude_pk):
        taken |= label_keys(other.key, other.aliases)
    if taken & set(keys):
        raise LabelConflict("Another room already uses this name or alias.")


def list_rooms(*, actor, workspace):
    """The workspace's rooms through `for_actor()`, in display order."""
    return list(Room.objects.for_actor(actor, workspace=workspace).order_by("sort_order", "name"))


def _room_audit(action, *, actor, room, metadata=None):
    record_audit(
        action=action,
        actor=actor,
        workspace=room.workspace,
        resource=room,
        metadata={"room_id": str(room.pk), "key": room.key, **(metadata or {})},
    )


@transaction.atomic
def create_room(*, actor, workspace, channel, name, aliases=None, sort_order=None):
    """Create a room in the owner's personal workspace (owner, web only).
    Audited `home.room_created` (id and key only)."""
    require_rooms_owner_web(actor=actor, workspace=workspace, channel=channel)
    name, key = clean_label(name, max_chars=ROOM_NAME_MAX_CHARS)
    aliases, alias_keys = clean_aliases(aliases)
    if key in alias_keys:
        raise LabelConflict("An alias repeats the room's name.")
    locked = _lock_workspace(workspace)
    _check_room_keys(locked, [key, *alias_keys])
    if sort_order is None:
        last = Room.objects.filter(workspace=locked).order_by("-sort_order").first()
        sort_order = (last.sort_order + 1) if last is not None else 0
    room = Room.objects.create(
        workspace=locked, name=name, key=key, aliases=aliases, sort_order=sort_order
    )
    _room_audit("home.room_created", actor=actor, room=room, metadata={"alias_count": len(aliases)})
    return room


UNSET = object()


@transaction.atomic
def update_room(*, actor, room, channel, name=UNSET, aliases=UNSET, sort_order=UNSET):
    """Rename a room, replace its aliases or move it (owner, web only).
    Audited `home.room_renamed` (old and new key) when the name changes and
    `home.room_changed` for aliases / order. A no-op writes nothing."""
    require_rooms_owner_web(actor=actor, workspace=room.workspace, channel=channel)
    locked_ws = _lock_workspace(room.workspace)
    locked = Room.objects.select_for_update().get(pk=room.pk, workspace=locked_ws)
    new_name, new_key = locked.name, locked.key
    if name is not UNSET:
        new_name, new_key = clean_label(name, max_chars=ROOM_NAME_MAX_CHARS)
    new_aliases = list(locked.aliases or [])
    if aliases is not UNSET:
        new_aliases, _ = clean_aliases(aliases)
    alias_keys = [phrase_key(alias) for alias in new_aliases]
    if new_key in alias_keys:
        raise LabelConflict("An alias repeats the room's name.")
    _check_room_keys(locked_ws, [new_key, *alias_keys], exclude_pk=locked.pk)
    old_key = locked.key
    changed = []
    if new_name != locked.name:
        locked.name, locked.key = new_name, new_key
        changed += ["name", "key"]
    if new_aliases != list(locked.aliases or []):
        locked.aliases = new_aliases
        changed.append("aliases")
    if sort_order is not UNSET and sort_order != locked.sort_order:
        locked.sort_order = sort_order
        changed.append("sort_order")
    if not changed:
        return locked
    locked.save(update_fields=[*changed, "updated_at"])
    if "name" in changed:
        _room_audit("home.room_renamed", actor=actor, room=locked, metadata={"old_key": old_key})
    other = sorted({"aliases", "sort_order"} & set(changed))
    if other:
        _room_audit(
            "home.room_changed",
            actor=actor,
            room=locked,
            metadata={"fields": other, "alias_count": len(locked.aliases)},
        )
    return locked


def delete_room(*, actor, room, channel):
    """Delete a room nothing uses (owner, web only). A room in use is
    `room.in_use`: the PROTECT foreign keys decide, so a later referencing
    model (branch 5's home entities) is covered without this app importing
    it. Audited `home.room_deleted`."""
    require_rooms_owner_web(actor=actor, workspace=room.workspace, channel=channel)
    try:
        with transaction.atomic():
            locked = Room.objects.select_for_update().get(pk=room.pk)
            room_id, key, workspace = str(locked.pk), locked.key, locked.workspace
            locked.delete()
            record_audit(
                action="home.room_deleted",
                actor=actor,
                workspace=workspace,
                metadata={"room_id": room_id, "key": key},
            )
    except ProtectedError as exc:
        raise RoomInUse("The room is still in use.") from exc
