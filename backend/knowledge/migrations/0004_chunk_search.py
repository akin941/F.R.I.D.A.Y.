"""Phase 8B branch 4 (brief D8): the generated `search` tsvector column of
`KnowledgeChunk` (content only) and its GIN index. Raw SQL, outside the
model state, like `memory` 0014. Reversible."""

from django.db import migrations

CHUNK_SEARCH = "to_tsvector('turkish', content) || to_tsvector('simple', content)"


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge", "0003_soft_delete"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE knowledge_knowledgechunk ADD COLUMN search tsvector "
                f"GENERATED ALWAYS AS ({CHUNK_SEARCH}) STORED",
                "CREATE INDEX knowledge_knowledgechunk_search_gin "
                "ON knowledge_knowledgechunk USING gin (search)",
            ],
            reverse_sql=[
                "DROP INDEX IF EXISTS knowledge_knowledgechunk_search_gin",
                "ALTER TABLE knowledge_knowledgechunk DROP COLUMN IF EXISTS search",
            ],
        ),
    ]
