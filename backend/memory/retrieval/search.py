"""SQL fragments shared by the hybrid retriever and `knowledge.context`
(Phase 8B branch 4, brief D8). No query runs here.

Full text. The generated `search` column of each table (migrations
`memory` 0014, `knowledge` 0004) is matched against
`websearch_to_tsquery('turkish', q) || websearch_to_tsquery('simple', q)`:
both configs, OR-ed. `websearch_to_tsquery` ANDs every word, which suits
an explicit search (`"payments api" -heroku`) but matches nothing for a
chat question ("Y firmasının deploy'u hangi servise bağlı?" would need
every word in one row). So a query with no search syntax (no double
quote, no `-word`, no `or`) keeps the parser's lexemes and stop-word
handling but joins them with `|`; ranking (`ts_rank_cd`) then prefers rows
that match more of them. A query with search syntax is used as written.

Vectors. `CastCosineDistance` renders `(embedding)::vector(<dim>) <=>
'<query>'::vector(<dim>)`, the expression the per-provider HNSW index is
built on (`memory.retrieval.indexes`); callers add the provider and
`vector_dims` predicates so the partial index applies.
"""

import re

from django.db.models import BooleanField, F, FloatField, Func, IntegerField
from django.db.models.expressions import RawSQL

_EXPLICIT_SYNTAX = re.compile(r'"|(^|\s)-\S|\bor\b', re.IGNORECASE)
_JOINERS = r" (&|<->|<[0-9]+>) "


def is_explicit_search(query_text):
    return bool(_EXPLICIT_SYNTAX.search(query_text or ""))


def tsquery_sql(query_text):
    """`(sql, params)` of the OR-ed two-config tsquery for `query_text`."""
    if is_explicit_search(query_text):
        sql = "(websearch_to_tsquery('turkish', %s) || websearch_to_tsquery('simple', %s))"
    else:
        sql = (
            f"(regexp_replace(websearch_to_tsquery('turkish', %s)::text, '{_JOINERS}', ' | ', 'g')"
            "::tsquery || "
            f"regexp_replace(websearch_to_tsquery('simple', %s)::text, '{_JOINERS}', ' | ', 'g')"
            "::tsquery)"
        )
    return sql, [query_text, query_text]


def fulltext_match(table, query_text):
    """A boolean expression: `<table>.search @@ <tsquery>`."""
    sql, params = tsquery_sql(query_text)
    return RawSQL(f'"{table}"."search" @@ {sql}', params, output_field=BooleanField())


def fulltext_rank(table, query_text):
    sql, params = tsquery_sql(query_text)
    return RawSQL(f'ts_rank_cd("{table}"."search", {sql})', params, output_field=FloatField())


class VectorDims(Func):
    """`vector_dims(embedding)` (pgvector): the dimension guard."""

    function = "vector_dims"
    output_field = IntegerField()


def vector_literal(vector):
    return "[" + ",".join(format(float(v), ".9g") for v in vector) + "]"


class CastCosineDistance(Func):
    """`(<column>)::vector(<dim>) <=> '<query>'::vector(<dim>)`."""

    output_field = FloatField()

    def __init__(self, column, query_vector, dimension):
        self.dimension = int(dimension)
        self.literal = vector_literal(query_vector)
        super().__init__(F(column))

    def as_sql(self, compiler, connection, **extra_context):
        column_sql, params = compiler.compile(self.source_expressions[0])
        dim = self.dimension
        return (
            f"(({column_sql})::vector({dim}) <=> %s::vector({dim}))",
            [*params, self.literal],
        )


def vector_ranked(queryset, *, column="embedding", query_vector, provider_id, limit):
    """`queryset` restricted to comparable rows (same provider, non-null,
    same dimension: the three guards) and ordered by the cast distance."""
    dim = len(query_vector)
    return (
        queryset.filter(**{f"{column}__isnull": False, "embedding_provider_id": provider_id})
        .annotate(_dims=VectorDims(column))
        .filter(_dims=dim)
        .annotate(_distance=CastCosineDistance(column, query_vector, dim))
        .order_by("_distance", "id")[:limit]
    )


def fulltext_ranked(queryset, *, table, query_text, limit):
    """`queryset` restricted to full-text matches, best `ts_rank_cd` first."""
    return (
        queryset.filter(fulltext_match(table, query_text))
        .annotate(_fts_rank=fulltext_rank(table, query_text))
        .order_by("-_fts_rank", "id")[:limit]
    )
