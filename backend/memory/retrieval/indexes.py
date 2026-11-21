"""Per-provider HNSW expression indexes over the untyped vector columns
(Phase 8B branch 4, brief D8).

The MVP 3 policy keeps `embedding` untyped, so a provider swap never orphans
stored vectors. HNSW needs a typed dimension, so each active embedding
provider gets one **partial expression index** per table:

    CREATE INDEX [CONCURRENTLY] IF NOT EXISTS <table>_hnsw_<provider8>
      ON <table> USING hnsw ((embedding::vector(<dim>)) vector_cosine_ops)
      WHERE embedding_provider_id = '<provider uuid>' AND vector_dims(embedding) = <dim>;

The `vector_dims` term is part of the predicate so that a stray row of
another width under the same provider can never break the cast (it is
simply not indexed); `memory.retrieval.vectors` emits exactly the same two
predicates, so the planner proves the partial index applies.

pgvector's HNSW on `vector` supports at most 2000 dimensions: a provider
above that gets no index, a warning, and a `skipped_dimension` entry;
retrieval over it stays an exact scan under the same guards (never a
silent `halfvec` fallback).

`build_vector_indexes()` is idempotent: an index whose definition already
names the right dimension is left alone, one whose dimension changed is
dropped and rebuilt. Table names are code constants and the provider id is
a UUID rendered by Python, so nothing user-supplied reaches the DDL.
"""

import logging
import uuid
from dataclasses import dataclass

from django.db import connection

from ai_core.models import ModelProvider

logger = logging.getLogger(__name__)

# The three tables with an untyped `embedding` + `embedding_provider_id`.
# `knowledge_knowledgechunk` is named as a table only: `memory` never
# imports `knowledge`.
INDEXED_TABLES = ("memory_memory", "memory_memoryfact", "knowledge_knowledgechunk")
HNSW_MAX_DIMENSIONS = 2000

OK = "ok"
CREATED = "created"
REBUILT = "rebuilt"
MISSING = "missing"
STALE = "stale"
SKIPPED_DIMENSION = "skipped_dimension"
SKIPPED_NO_DIMENSION = "skipped_no_dimension"


@dataclass(frozen=True)
class IndexEntry:
    table: str
    provider_id: str
    provider_name: str
    dimension: int
    name: str
    status: str


def index_name(table, provider_id):
    return f"{table}_hnsw_{uuid.UUID(str(provider_id)).hex[:8]}"


def _stored_dimension(provider_id):
    """The most common stored width under `provider_id` across the tables,
    or 0 when nothing is stored yet."""
    counts = {}
    with connection.cursor() as cursor:
        for table in INDEXED_TABLES:
            cursor.execute(
                f"SELECT vector_dims(embedding), count(*) FROM {table} "  # noqa: S608 (code constant)
                "WHERE embedding_provider_id = %s AND embedding IS NOT NULL "
                "GROUP BY 1",
                [str(provider_id)],
            )
            for dims, count in cursor.fetchall():
                counts[dims] = counts.get(dims, 0) + count
    if not counts:
        return 0
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def provider_dimension(provider):
    """The provider's configured dimension, else its stored one."""
    return int(provider.dimension or 0) or _stored_dimension(provider.pk)


def active_embedding_providers():
    return list(
        ModelProvider.objects.filter(
            capability=ModelProvider.Capability.EMBEDDING, is_active=True
        ).order_by("name")
    )


def _existing_definitions():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() "
            "AND tablename = ANY(%s)",
            [list(INDEXED_TABLES)],
        )
        return dict(cursor.fetchall())


def _definition_matches(definition, dimension):
    return f"vector({dimension})" in definition and "hnsw" in definition


def _create_sql(table, name, provider_id, dimension, *, concurrently):
    provider_literal = str(uuid.UUID(str(provider_id)))
    dim = int(dimension)
    mode = "CONCURRENTLY " if concurrently else ""
    return (
        f"CREATE INDEX {mode}IF NOT EXISTS {name} ON {table} "
        f"USING hnsw ((embedding::vector({dim})) vector_cosine_ops) "
        f"WHERE embedding_provider_id = '{provider_literal}'::uuid "
        f"AND vector_dims(embedding) = {dim}"
    )


def _plan(providers):
    """Yield `(provider, dimension, table, name)` per provider and table,
    plus the dimension verdict."""
    for provider in providers:
        dimension = provider_dimension(provider)
        for table in INDEXED_TABLES:
            yield provider, dimension, table, index_name(table, provider.pk)


def index_status(providers=None):
    """`--check` (and preflight `retrieval_indexes`, branch 10): one entry
    per active embedding provider and table, without writing anything."""
    providers = active_embedding_providers() if providers is None else providers
    existing = _existing_definitions()
    entries = []
    for provider, dimension, table, name in _plan(providers):
        if dimension <= 0:
            status = SKIPPED_NO_DIMENSION
        elif dimension > HNSW_MAX_DIMENSIONS:
            status = SKIPPED_DIMENSION
        elif name not in existing:
            status = MISSING
        elif not _definition_matches(existing[name], dimension):
            status = STALE
        else:
            status = OK
        entries.append(IndexEntry(table, str(provider.pk), provider.name, dimension, name, status))
    return entries


def build_vector_indexes(*, concurrently=None, providers=None):
    """Create the missing indexes and rebuild stale ones. `CONCURRENTLY`
    unless inside a transaction (tests, `post_migrate` under an atomic
    block), where Postgres refuses it. Returns the `IndexEntry` list."""
    if concurrently is None:
        concurrently = not connection.in_atomic_block
    entries = []
    for entry in index_status(providers):
        status = entry.status
        if status == SKIPPED_DIMENSION:
            logger.warning(
                "build_vector_indexes: provider %s stores %s dimensions, above the HNSW "
                "limit of %s; no index on %s (exact scan).",
                entry.provider_name,
                entry.dimension,
                HNSW_MAX_DIMENSIONS,
                entry.table,
            )
        elif status in (MISSING, STALE):
            with connection.cursor() as cursor:
                if status == STALE:
                    mode = "CONCURRENTLY " if concurrently else ""
                    cursor.execute(f"DROP INDEX {mode}IF EXISTS {entry.name}")
                cursor.execute(
                    _create_sql(
                        entry.table,
                        entry.name,
                        entry.provider_id,
                        entry.dimension,
                        concurrently=concurrently,
                    )
                )
            status = CREATED if status == MISSING else REBUILT
        entries.append(
            IndexEntry(
                entry.table,
                entry.provider_id,
                entry.provider_name,
                entry.dimension,
                entry.name,
                status,
            )
        )
    return entries


def build_after_migrate(sender=None, **kwargs):
    """`post_migrate` hook: build what is missing; never fail a migrate
    (the `--check` and preflight report what is left)."""
    using = kwargs.get("using", "default")
    if using != connection.alias:
        return
    try:
        build_vector_indexes()
    except Exception:  # noqa: BLE001 -- a migrate must not fail on an index build
        logger.exception("build_vector_indexes after migrate failed; run it by hand.")
