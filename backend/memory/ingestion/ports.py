"""The one door into episode storage (Phase 8A brief D1).

Producers that live in apps `memory` may not import (`harness`,
`connectors`, the hook endpoint) call `record_episode` -- exactly like
`memory.services.record_work_log` today. Nobody else writes `Episode` rows.

    record_episode(*, source, kind, external_id, occurred_at, mapping_key,
                   owner_text="", context_text="", metadata=None,
                   sensitivity="normal") -> EpisodeResult

In order: the source must exist, be enabled and not paused
(`dropped_paused`); the mapping key must resolve to a workspace through an
enabled rule (`dropped_unmapped`, counted, the key kept only as a hash plus a
scrubbed short form -- the item itself is not kept); `(source, external_id)`
must be new (`duplicate`); then every text and metadata value is scrubbed
(secrets, PII, the target workspace's scrub terms) **before** the row is
written, and truncated to its cap **after** scrubbing (a placeholder is
never cut into a partial secret). `owner_text` is the only field extraction
may ever quote; the producer decides which of its text is owner-authored
(D5) and the port records it as such.
"""

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from memory.ingestion import services
from memory.ingestion.mapping import owner_of, resolve_workspace, unmapped_entry
from memory.ingestion.scrubber import merge_reports, scrub_text, scrub_value
from memory.models import Episode, IngestionSource
from workspaces.models import Workspace

logger = logging.getLogger(__name__)

STORED = "stored"
DROPPED_UNMAPPED = "dropped_unmapped"
DROPPED_PAUSED = "dropped_paused"
DUPLICATE = "duplicate"

_EXTERNAL_ID = re.compile(r"\A[A-Za-z0-9:._@/#+\-]{1,255}\Z")
_KIND = re.compile(r"\A[a-z0-9_.\-]{1,40}\Z")


@dataclass(frozen=True)
class EpisodeResult:
    status: str
    episode_id: str | None = None

    @property
    def stored(self):
        return self.status == STORED


class InvalidEpisode(ValueError):
    """A producer bug (bad kind, external id, timestamp, sensitivity):
    raised, never silently stored."""


def _resolve_source(source):
    """An IngestionSource, or a kind string naming the installation owner's
    source of that kind (one per kind in 8A, in their personal
    workspace). None when no such source is registered."""
    if isinstance(source, IngestionSource):
        return IngestionSource.objects.filter(pk=source.pk).select_related("workspace").first()
    if isinstance(source, str):
        return (
            IngestionSource.objects.filter(
                kind=source,
                workspace__type=Workspace.Type.PERSONAL,
                workspace__owner__is_owner=True,
            )
            .select_related("workspace")
            .order_by("created_at")
            .first()
        )
    return None


def source_accepting(source) -> bool:
    """Whether `source` (an IngestionSource or a kind string) is registered,
    enabled and not paused. Producers ask before doing expensive work (a
    clone, a calendar read) so a paused source costs nothing; the port
    re-checks on every item anyway."""
    resolved = _resolve_source(source)
    return bool(resolved and resolved.is_accepting)


def _safe_external_id(external_id):
    """Producers pass ids like `commit:<sha>` or `harness:<uuid>`. A value
    outside the strict charset, or one the scrubber would touch, is stored as
    its sha256 so no raw secret can ride in through the dedupe key."""
    external_id = str(external_id or "")
    if not external_id:
        raise InvalidEpisode("external_id is required")
    if _EXTERNAL_ID.match(external_id) and not scrub_text(external_id).hits:
        return external_id
    return "sha256:" + hashlib.sha256(external_id.encode("utf-8", "replace")).hexdigest()


def _bounded(text, terms):
    result = scrub_text(text or "", terms=terms)
    return result.text[: Episode.MAX_TEXT_CHARS], result.report


def record_episode(
    *,
    source,
    kind,
    external_id,
    occurred_at,
    mapping_key,
    owner_text="",
    context_text="",
    metadata=None,
    sensitivity="normal",
) -> EpisodeResult:
    if not isinstance(kind, str) or not _KIND.match(kind):
        raise InvalidEpisode("kind must be a short lowercase identifier")
    if not isinstance(occurred_at, datetime) or timezone.is_naive(occurred_at):
        raise InvalidEpisode("occurred_at must be an aware datetime")
    if sensitivity not in Episode.Sensitivity.values:
        raise InvalidEpisode("unknown sensitivity")
    if metadata is not None and not isinstance(metadata, dict):
        raise InvalidEpisode("metadata must be a dict")
    external_id = _safe_external_id(external_id)

    source = _resolve_source(source)
    if source is None:
        # Unregistered source: nothing is ingested (ADR-012 decision 1).
        return EpisodeResult(DROPPED_PAUSED)
    if not source.is_accepting:
        services.bump_counter(source, "dropped_paused")
        return EpisodeResult(DROPPED_PAUSED)

    workspace = resolve_workspace(source, mapping_key)
    if workspace is None:
        services.record_unmapped(source, unmapped_entry(mapping_key))
        return EpisodeResult(DROPPED_UNMAPPED)

    if Episode.all_objects.filter(source=source, external_id=external_id).exists():
        services.bump_counter(source, "duplicate")
        return EpisodeResult(DUPLICATE)

    terms = services.scrub_terms_for(workspace)
    if owner_of(source).pk != workspace.owner_id:
        # Defence in depth: resolve_workspace already re-checked ownership.
        services.record_unmapped(source, unmapped_entry(mapping_key))
        return EpisodeResult(DROPPED_UNMAPPED)
    owner_clean, owner_report = _bounded(owner_text, terms)
    context_clean, context_report = _bounded(context_text, terms)
    metadata_clean, metadata_report = scrub_value(metadata or {}, terms=terms)
    report = merge_reports(owner_report, context_report, metadata_report)
    token_estimate = math.ceil((len(owner_clean) + len(context_clean)) / 4)

    try:
        with transaction.atomic():
            episode = Episode.all_objects.create(
                workspace=workspace,
                source=source,
                kind=kind,
                external_id=external_id,
                occurred_at=occurred_at,
                owner_text=owner_clean,
                context_text=context_clean,
                metadata=metadata_clean,
                sensitivity=sensitivity,
                scrub_report=report,
                token_estimate=token_estimate,
                expires_at=services.expires_at_for(workspace),
            )
    except IntegrityError:
        # Lost a race against the same (source, external_id).
        services.bump_counter(source, "duplicate")
        return EpisodeResult(DUPLICATE)
    services.bump_counter(source, "stored")
    services.record_scrub_hits(source, report)
    return EpisodeResult(STORED, str(episode.id))
