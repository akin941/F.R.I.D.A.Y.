"""Phase 12 D8: wake-route aliases -- the owner's own phrases for the
code-defined routes (`assistant.wake_routes`).

Created, reworded and deleted only by the workspace owner over the web
(`core.owner_web`), refused inside an unattended run (`alias_write`), and
audited (`assistant.wake_alias.created` / `.updated` / `.deleted`). D8's
rules: normalized like a transcript, 2-40 characters, unique per
workspace, refused when it equals or word-prefixes another route's phrase
or alias, at most five per route. No voice turn, tool, skill, model or
connector reaches this module (the walk in
`assistant/tests/test_wake_route_aliases.py`).

Kept out of `assistant.services`: that module is imported by other apps,
and `assistant.wake_routes` reads `proactive.api`, which only `assistant`,
`hud` and `ops` may reach (Phase 12 D19).
"""

from django.db import transaction

from assistant import wake_routes
from assistant.models import WakeRouteAlias
from audit.services import record_audit
from core.owner_web import OwnerWebRequired, require_owner_web
from core.unattended import deny_if_unattended


class WakeAliasError(Exception):
    """A refused alias write; each subclass carries its closed `code`
    (`core.error_codes`)."""

    code = "wake_alias.conflict"


class WakeAliasUnknownRoute(WakeAliasError):
    code = "wake_alias.unknown_route"


class WakeAliasInvalidLength(WakeAliasError):
    code = "wake_alias.invalid_length"


class WakeAliasConflict(WakeAliasError):
    code = "wake_alias.conflict"


class WakeAliasLimit(WakeAliasError):
    code = "wake_alias.limit"


class WakeAliasPermissionDenied(Exception):
    """Not the owner over the web: `owner_required` / `channel_forbidden`
    (the generic `core.owner_web` codes, so not a `WakeAliasError`)."""

    def __init__(self, message, code="owner_required"):
        self.code = code
        super().__init__(message)


def _require_alias_owner_web(*, actor, workspace, channel):
    try:
        require_owner_web(actor=actor, workspace=workspace, channel=channel)
    except OwnerWebRequired as exc:
        raise WakeAliasPermissionDenied(str(exc), code=exc.code) from None


def _lock_workspace(workspace):
    # Serialize alias writes per workspace so the limit and the clash checks
    # see every committed row.
    type(workspace).objects.select_for_update().filter(pk=workspace.pk).first()


def _clean_alias(*, workspace, route, phrase, exclude_id=None):
    """Validate one alias against D8's rules; returns the normalized phrase."""
    if wake_routes.get_route(route) is None:
        raise WakeAliasUnknownRoute("Unknown wake route.")
    normalized = wake_routes.normalize_utterance(phrase if isinstance(phrase, str) else "")
    if not wake_routes.ALIAS_MIN_CHARS <= len(normalized) <= wake_routes.ALIAS_MAX_CHARS:
        raise WakeAliasInvalidLength(
            f"An alias is {wake_routes.ALIAS_MIN_CHARS}-{wake_routes.ALIAS_MAX_CHARS} "
            "characters after normalization."
        )
    others = WakeRouteAlias.objects.filter(workspace=workspace)
    if exclude_id is not None:
        others = others.exclude(pk=exclude_id)
    taken = [(r.name, p) for r in wake_routes.ROUTES for p in r.default_phrases]
    taken += list(others.values_list("route", "phrase"))
    for owner_route, other in taken:
        if wake_routes.match_key(normalized) == wake_routes.match_key(other):
            raise WakeAliasConflict("This phrase is already in use.")
        if owner_route != route and wake_routes.phrase_clash(normalized, other):
            raise WakeAliasConflict("This phrase clashes with another route's phrase.")
    if exclude_id is None and others.filter(route=route).count() >= (
        wake_routes.MAX_ALIASES_PER_ROUTE
    ):
        raise WakeAliasLimit(f"A route has at most {wake_routes.MAX_ALIASES_PER_ROUTE} aliases.")
    return normalized


def create_wake_alias(*, actor, channel, workspace, route, phrase):
    """The owner adds an alias over the web (D8). Audited
    `assistant.wake_alias.created`."""
    deny_if_unattended("alias_write")
    _require_alias_owner_web(actor=actor, workspace=workspace, channel=channel)
    with transaction.atomic():
        _lock_workspace(workspace)
        normalized = _clean_alias(workspace=workspace, route=route, phrase=phrase)
        alias = WakeRouteAlias.objects.create(
            workspace=workspace, route=route, phrase=normalized, created_by=actor
        )
        record_audit(
            action="assistant.wake_alias.created",
            actor=actor,
            workspace=workspace,
            resource=alias,
            metadata={"route": route, "phrase": normalized},
        )
    return alias


def update_wake_alias(*, actor, channel, alias, phrase):
    """The owner rewords an alias over the web. Audited
    `assistant.wake_alias.updated`."""
    deny_if_unattended("alias_write")
    workspace = alias.workspace
    _require_alias_owner_web(actor=actor, workspace=workspace, channel=channel)
    with transaction.atomic():
        _lock_workspace(workspace)
        normalized = _clean_alias(
            workspace=workspace, route=alias.route, phrase=phrase, exclude_id=alias.pk
        )
        previous = alias.phrase
        alias.phrase = normalized
        alias.save(update_fields=["phrase", "updated_at"])
        record_audit(
            action="assistant.wake_alias.updated",
            actor=actor,
            workspace=workspace,
            resource=alias,
            metadata={"route": alias.route, "phrase": normalized, "previous": previous},
        )
    return alias


def delete_wake_alias(*, actor, channel, alias):
    """The owner removes an alias over the web. Audited
    `assistant.wake_alias.deleted`."""
    deny_if_unattended("alias_write")
    workspace = alias.workspace
    _require_alias_owner_web(actor=actor, workspace=workspace, channel=channel)
    with transaction.atomic():
        record_audit(
            action="assistant.wake_alias.deleted",
            actor=actor,
            workspace=workspace,
            resource=alias,
            metadata={"route": alias.route, "phrase": alias.phrase},
        )
        alias.delete()
