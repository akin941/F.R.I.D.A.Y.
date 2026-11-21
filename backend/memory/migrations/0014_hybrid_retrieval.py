"""Phase 8B branch 4 (brief D8): the fact embedding lease fields and the
generated `search` tsvector columns with their GIN indexes.

The `search` columns are raw SQL (Django's `GeneratedField` cannot express
`setweight` portably) and stay out of the model state: inserts never name
them, and `memory.retrieval` reads them through `RawSQL`. One expression per
table, because the text columns differ: `turkish` for stemming, `simple`
for English words and code identifiers the Turkish stemmer would mangle.
Reversible.
"""

from django.db import migrations, models

MEMORY_SEARCH = (
    "setweight(to_tsvector('turkish', title), 'A') "
    "|| to_tsvector('turkish', content) "
    "|| to_tsvector('simple', title || ' ' || content)"
)
FACT_SEARCH = "to_tsvector('turkish', statement) || to_tsvector('simple', statement)"


class Migration(migrations.Migration):

    dependencies = [
        ("memory", "0013_memory_eval_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="memoryfact",
            name="claim_token",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memoryfact",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memoryfact",
            name="retry_count",
            field=models.PositiveIntegerField(db_default=0, default=0),
        ),
        migrations.RunSQL(
            sql=[
                "ALTER TABLE memory_memory ADD COLUMN search tsvector "
                f"GENERATED ALWAYS AS ({MEMORY_SEARCH}) STORED",
                "CREATE INDEX memory_memory_search_gin ON memory_memory USING gin (search)",
                "ALTER TABLE memory_memoryfact ADD COLUMN search tsvector "
                f"GENERATED ALWAYS AS ({FACT_SEARCH}) STORED",
                "CREATE INDEX memory_memoryfact_search_gin ON memory_memoryfact USING gin (search)",
            ],
            reverse_sql=[
                "DROP INDEX IF EXISTS memory_memoryfact_search_gin",
                "ALTER TABLE memory_memoryfact DROP COLUMN IF EXISTS search",
                "DROP INDEX IF EXISTS memory_memory_search_gin",
                "ALTER TABLE memory_memory DROP COLUMN IF EXISTS search",
            ],
        ),
    ]
