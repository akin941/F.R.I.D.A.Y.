from django.apps import AppConfig
from django.db.models.signals import post_migrate


class MemoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "memory"

    def ready(self):
        # Phase 8B branch 4 (D8): the per-provider HNSW expression indexes
        # are (re)built after every migrate; idempotent, never fails it.
        from memory.probes import register
        from memory.retrieval.indexes import build_after_migrate

        # Phase 12 D1: the calendar, learning-card and sessions probes.
        register()

        post_migrate.connect(
            build_after_migrate, sender=self, dispatch_uid="memory.build_vector_indexes"
        )
