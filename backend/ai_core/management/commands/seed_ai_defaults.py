"""Idempotent seed for AI defaults: jarvis_system, jarvis_memory_extraction,
jarvis_router_intent and jarvis_router_task_draft prompts, and providers.

Uses get_or_create so re-running never duplicates records and never clobbers
pricing/priority edits made in the admin.
"""

import os

from django.core.management.base import BaseCommand

from ai_core.models import ModelProvider, PromptTemplate, PromptVersion

JARVIS_SYSTEM_V1 = (
    "You are Jarvis OS, a concise and reliable personal AI operating layer. "
    "Answer clearly, respect workspace boundaries, and do not claim access to "
    "memory, files, tools, or integrations that are not available yet."
)

# Schema mirrors memory.extraction.SCHEMA_VERSION/parse_candidates (MVP 2 §3).
# ai_core must not import the memory app (ADR-003 one-way dependency), so
# this prompt content intentionally duplicates the shape in prose rather than
# referencing it; a lineage test keeps the two in sync in intent, not import.
JARVIS_MEMORY_EXTRACTION_V1 = (
    "You distill durable, useful memories from a conversation excerpt. Read "
    "the messages and output ONLY a single JSON object, no prose, no code "
    "fences, matching exactly this shape:\n"
    '{"version": 1, "candidates": '
    '[{"type": "semantic|episodic|preference|procedural", '
    '"title": "short label", "content": "the memory, one clear statement"}]}\n'
    "Rules: at most 10 candidates; omit anything not worth remembering long "
    "term (return an empty candidates list if nothing qualifies); never "
    "invent facts not present in the conversation; title <= 255 characters, "
    "content <= 4000 characters; output nothing outside the JSON object."
)

# Schema mirrors assistant.intent.parse_intent (MVP 6 §1). ai_core must not
# import the assistant app (ADR-003 one-way dependency), so this prompt
# content intentionally duplicates the shape in prose rather than
# referencing it — same discipline as JARVIS_MEMORY_EXTRACTION_V1 above.
JARVIS_ROUTER_INTENT_V1 = (
    "You classify a single user chat message into exactly one intent. Read "
    "ONLY the latest user message and output ONLY a single JSON object, no "
    "prose, no code fences, matching exactly this shape:\n"
    '{"intent": "chat|memory_query|knowledge_query|task_draft|tool_request", '
    '"tool_name": null}\n'
    "Rules: chat is the default for ordinary conversation; memory_query is "
    "for questions about the user's own remembered/curated notes; "
    "knowledge_query is for questions about the user's knowledge base/"
    "documents (e.g. summarize, what does X say); task_draft is for messages "
    "describing a task/reminder/to-do to create; tool_request is for "
    "messages naming an action a registered tool performs (today: only "
    "'vault_export', for exporting to the Obsidian vault) — set tool_name to "
    "the matched tool's name ONLY for tool_request, otherwise tool_name must "
    "be null; output nothing outside the JSON object."
)

# Schema mirrors assistant.task_draft.parse_task_draft (MVP 6 §3). ai_core
# must not import the assistant app (ADR-003 one-way dependency), so this
# prompt content intentionally duplicates the shape in prose rather than
# referencing it — same discipline as the two prompts above. The caller
# (assistant.services._extract_task_draft) appends a per-call reference-date
# line to this content before sending it — this string is the fixed,
# versioned part only.
JARVIS_ROUTER_TASK_DRAFT_V1 = (
    "You extract a single task/reminder from a user chat message already "
    "classified as describing one. Read ONLY the latest user message and "
    "output ONLY a single JSON object, no prose, no code fences, matching "
    "exactly this shape:\n"
    '{"title": "short label", "due_date": "YYYY-MM-DD" | null}\n'
    "Rules: title <= 255 characters, a short imperative label (not the "
    "user's whole sentence); due_date is null if the message names no date; "
    'resolve relative dates ("tomorrow", "Friday") ONLY against the '
    "reference date given below the schema, if one is given — never guess a "
    "date without it; output nothing outside the JSON object."
)


class Command(BaseCommand):
    help = "Seed the jarvis_system prompt and LLM providers (idempotent)."

    def handle(self, *args, **options):
        template, created = PromptTemplate.objects.get_or_create(
            name="jarvis_system", defaults={"purpose": "chat"}
        )
        version, v_created = PromptVersion.objects.get_or_create(
            template=template,
            version=1,
            defaults={"content": JARVIS_SYSTEM_V1, "changelog": "Initial version (MVP 1)."},
        )
        if template.active_version_id is None:
            template.active_version = version
            template.save(update_fields=["active_version"])
        self._log("jarvis_system prompt", created or v_created)

        extraction_template, created = PromptTemplate.objects.get_or_create(
            name="jarvis_memory_extraction", defaults={"purpose": "memory.extraction"}
        )
        extraction_version, v_created = PromptVersion.objects.get_or_create(
            template=extraction_template,
            version=1,
            defaults={
                "content": JARVIS_MEMORY_EXTRACTION_V1,
                "changelog": "Initial version (MVP 2).",
            },
        )
        if extraction_template.active_version_id is None:
            extraction_template.active_version = extraction_version
            extraction_template.save(update_fields=["active_version"])
        self._log("jarvis_memory_extraction prompt", created or v_created)

        router_intent_template, created = PromptTemplate.objects.get_or_create(
            name="jarvis_router_intent", defaults={"purpose": "router.intent"}
        )
        router_intent_version, v_created = PromptVersion.objects.get_or_create(
            template=router_intent_template,
            version=1,
            defaults={
                "content": JARVIS_ROUTER_INTENT_V1,
                "changelog": "Initial version (MVP 6).",
            },
        )
        if router_intent_template.active_version_id is None:
            router_intent_template.active_version = router_intent_version
            router_intent_template.save(update_fields=["active_version"])
        self._log("jarvis_router_intent prompt", created or v_created)

        task_draft_template, created = PromptTemplate.objects.get_or_create(
            name="jarvis_router_task_draft", defaults={"purpose": "router.task_draft"}
        )
        task_draft_version, v_created = PromptVersion.objects.get_or_create(
            template=task_draft_template,
            version=1,
            defaults={
                "content": JARVIS_ROUTER_TASK_DRAFT_V1,
                "changelog": "Initial version (MVP 6).",
            },
        )
        if task_draft_template.active_version_id is None:
            task_draft_template.active_version = task_draft_version
            task_draft_template.save(update_fields=["active_version"])
        self._log("jarvis_router_task_draft prompt", created or v_created)

        _, created = ModelProvider.objects.get_or_create(
            name="fake-local",
            defaults={
                "provider_type": ModelProvider.ProviderType.FAKE,
                "capability": ModelProvider.Capability.CHAT,
                "model_id": "fake-1",
                "priority": 0,  # first when AI_FAKE_PROVIDER=true; filtered out otherwise
                "is_active": True,
            },
        )
        self._log("fake-local provider", created)

        _, created = ModelProvider.objects.get_or_create(
            name="fake-embedding-local",
            defaults={
                "provider_type": ModelProvider.ProviderType.FAKE,
                "capability": ModelProvider.Capability.EMBEDDING,
                "model_id": "fake-embedding-1",
                "dimension": 32,
                "priority": 0,  # first when AI_FAKE_PROVIDER=true; filtered out otherwise
                "is_active": True,
            },
        )
        self._log("fake-embedding-local provider", created)

        if os.environ.get("ANTHROPIC_API_KEY"):
            _, created = ModelProvider.objects.get_or_create(
                name="anthropic-primary",
                defaults={
                    "provider_type": ModelProvider.ProviderType.ANTHROPIC,
                    "model_id": os.environ.get("ANTHROPIC_MODEL_ID", "claude-sonnet-5"),
                    "api_key_env_var": "ANTHROPIC_API_KEY",
                    "input_price_per_mtok": "3.0",
                    "output_price_per_mtok": "15.0",
                    "priority": 10,
                },
            )
            self._log("anthropic-primary provider", created)

        if os.environ.get("OPENAI_API_KEY"):
            _, created = ModelProvider.objects.get_or_create(
                name="openai-primary",
                defaults={
                    "provider_type": ModelProvider.ProviderType.OPENAI_COMPATIBLE,
                    "model_id": os.environ.get("OPENAI_MODEL_ID", "gpt-5-mini"),
                    "base_url": os.environ.get("OPENAI_BASE_URL", ""),
                    "api_key_env_var": "OPENAI_API_KEY",
                    "priority": 20,
                },
            )
            self._log("openai-primary provider", created)

            _, created = ModelProvider.objects.get_or_create(
                name="openai-embedding-primary",
                defaults={
                    "provider_type": ModelProvider.ProviderType.OPENAI_COMPATIBLE,
                    "capability": ModelProvider.Capability.EMBEDDING,
                    "model_id": os.environ.get(
                        "OPENAI_EMBEDDING_MODEL_ID", "text-embedding-3-small"
                    ),
                    "base_url": os.environ.get("OPENAI_BASE_URL", ""),
                    "api_key_env_var": "OPENAI_API_KEY",
                    # 1536 matches the default model_id above; an operator
                    # overriding OPENAI_EMBEDDING_MODEL_ID to a model with a
                    # different output size must override this too, or the
                    # provider's recorded dimension would silently mismatch
                    # the vectors OpenAICompatibleAdapter.embed() returns.
                    "dimension": int(os.environ.get("OPENAI_EMBEDDING_DIMENSION", "1536")),
                    "priority": 20,
                },
            )
            self._log("openai-embedding-primary provider", created)

        if os.environ.get("DEEPSEEK_API_KEY"):
            _, created = ModelProvider.objects.get_or_create(
                name="deepseek",
                defaults={
                    "provider_type": ModelProvider.ProviderType.OPENAI_COMPATIBLE,
                    "model_id": os.environ.get("DEEPSEEK_MODEL_ID", "deepseek-chat"),
                    "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                    "api_key_env_var": "DEEPSEEK_API_KEY",
                    "priority": 30,
                },
            )
            self._log("deepseek provider", created)

        self.stdout.write(self.style.SUCCESS("seed_ai_defaults completed."))

    def _log(self, label, created):
        self.stdout.write(f"  {label}: {'created' if created else 'already present'}")
