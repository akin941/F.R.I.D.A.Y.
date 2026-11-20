from django.conf import settings
from django.db import models

from core.models import TimestampedModel


class ModelProvider(TimestampedModel):
    """An LLM provider/model configuration.

    Security rule: real API keys are NEVER stored here — `api_key_env_var`
    holds only the *name* of the environment variable that carries the key.
    """

    class ProviderType(models.TextChoices):
        ANTHROPIC = "anthropic"
        OPENAI_COMPATIBLE = "openai_compatible"
        FAKE = "fake"

    class Capability(models.TextChoices):
        CHAT = "chat"
        EMBEDDING = "embedding"
        STT = "stt"  # speech-to-text (MVP 12)
        TTS = "tts"  # text-to-speech (MVP 12)

    name = models.CharField(max_length=100, unique=True)
    provider_type = models.CharField(max_length=30, choices=ProviderType.choices)
    capability = models.CharField(
        max_length=20, choices=Capability.choices, default=Capability.CHAT
    )
    # Only meaningful when capability="embedding"; 0 for chat providers.
    dimension = models.PositiveIntegerField(default=0)
    model_id = models.CharField(max_length=100)
    base_url = models.URLField(blank=True, default="")
    api_key_env_var = models.CharField(max_length=100, blank=True, default="")
    input_price_per_mtok = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    output_price_per_mtok = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    is_active = models.BooleanField(default=True)
    # Lower priority = tried first. The fake provider is seeded with priority 0
    # but is only selectable when settings.AI_FAKE_PROVIDER is on.
    priority = models.PositiveIntegerField(default=100)
    # D5 (Phase 5, ADR-010 decision 5): may this provider receive
    # personal-class data (mail, calendar, Home Assistant, sensitive-repo
    # reads)? Default False -- a cloud provider never qualifies by
    # accident. `seed_ai_defaults` creates only the local, no-network
    # providers with True; every other change goes through the audited
    # `manage.py set_provider_personal_data` (read-only in the admin).
    # `InferenceGateway.complete(..., personal_data=True)` selects ONLY
    # providers with this flag and fails closed when there are none.
    personal_data_allowed = models.BooleanField(default=False)
    # Phase 6 A3 (ADR-003 amendment 2026-11-20): audio is metered like text.
    # STT is priced per second of input audio, TTS per million input
    # characters; both 0 for local providers (which therefore never trip a
    # budget). A provider-reported cost (OpenRouter `usage.cost`) wins over
    # these configured prices when the response carries one.
    price_per_audio_second = models.DecimalField(max_digits=12, decimal_places=8, default=0)
    price_per_mchar = models.DecimalField(max_digits=10, decimal_places=4, default=0)

    class Meta:
        ordering = ["priority", "name"]

    @property
    def is_priced(self):
        """True when any configured price is non-zero (budget relevance)."""
        return any(
            price > 0
            for price in (
                self.input_price_per_mtok,
                self.output_price_per_mtok,
                self.price_per_audio_second,
                self.price_per_mchar,
            )
        )

    def __str__(self):
        return f"{self.name} ({self.model_id})"


class ModelTier(TimestampedModel):
    """Model-tier routing skeleton (phase 0, MASTER_PLAN §J).

    Maps a provider into a named cost/capability tier (cheap/mid/strong).
    Which purpose uses which tier lives in `settings.AI_PURPOSE_TIERS`
    (purpose -> tier name) — deliberately a settings dict, not a table,
    until it needs runtime editing (phase-0 brief §4). With no tier rows
    and an empty map, the gateway behaves exactly as before: priority
    order, `routing_reason` recorded as priority/fallback/only_provider.
    """

    class Tier(models.TextChoices):
        CHEAP = "cheap"
        MID = "mid"
        STRONG = "strong"

    tier = models.CharField(max_length=10, choices=Tier.choices)
    provider = models.ForeignKey(ModelProvider, on_delete=models.CASCADE, related_name="tiers")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tier", "provider"], name="unique_tier_provider")
        ]
        ordering = ["tier", "provider__priority", "provider__name"]

    def __str__(self):
        return f"{self.tier}: {self.provider.name}"


class AIBudget(TimestampedModel):
    """A spending cap enforced by the gateway BEFORE provider selection
    (Phase 6 A3, ADR-003 amendment 2026-11-20).

    Covers the purposes listed in `purposes`; each limit is optional (null =
    no cap for that window): `daily_limit_usd` (calendar day,
    `settings.TIME_ZONE`), `monthly_limit_usd` (calendar month) and
    `session_limit_usd` (all calls a caller tagged with the same
    `budget_session`, e.g. one voice session). Spend is the sum of
    `ModelUsage.estimated_cost` over the covered purposes -- install-wide,
    since Friday has one owner. A refused call is itself a `ModelUsage` row
    (`error_type="budget_exceeded"`, cost 0). Owner-editable in the admin;
    `seed_ai_defaults` creates the `audio` budget once and never overwrites
    an owner's edit."""

    name = models.CharField(max_length=50, unique=True)
    purposes = models.JSONField(default=list)
    daily_limit_usd = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    monthly_limit_usd = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    session_limit_usd = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PromptTemplate(TimestampedModel):
    name = models.CharField(max_length=100, unique=True)
    purpose = models.CharField(max_length=50, blank=True, default="")
    active_version = models.ForeignKey(
        "ai_core.PromptVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    def __str__(self):
        return self.name


class PromptVersion(TimestampedModel):
    """Immutable-by-discipline prompt content. Never edit a version in place —
    create the next version and repoint `active_version` instead."""

    template = models.ForeignKey(PromptTemplate, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    content = models.TextField()
    model_params = models.JSONField(default=dict, blank=True)
    changelog = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["template", "version"], name="unique_prompt_template_version"
            )
        ]
        ordering = ["template", "-version"]

    def __str__(self):
        return f"{self.template.name} v{self.version}"


class ModelUsage(TimestampedModel):
    """One row per gateway call — success, failure, timeout, fallback, streaming
    completion and client disconnect included. This is the cost/observability
    backbone (ADR-003); no gateway execution path may skip it.

    `provider` is nullable so the guarantee also holds when no provider is
    available at all (error_type="no_provider").
    """

    workspace = models.ForeignKey(
        "workspaces.Workspace", null=True, blank=True, on_delete=models.SET_NULL
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    provider = models.ForeignKey(ModelProvider, null=True, blank=True, on_delete=models.PROTECT)
    prompt_version = models.ForeignKey(
        PromptVersion, null=True, blank=True, on_delete=models.SET_NULL
    )
    purpose = models.CharField(max_length=50)
    request_id = models.CharField(max_length=64, blank=True, default="")
    backend = models.CharField(max_length=30, default="in_process")
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    success = models.BooleanField(default=False)
    error_type = models.CharField(max_length=50, blank=True, default="")
    # Why THIS provider served the call (phase 0, §J/§L auditability):
    # "tier:<name>" | "priority" | "fallback:<n>" | "only_provider";
    # "personal_data_required" on a no-provider failure caused by the D5
    # personal-data filter (Phase 5). External-CLI rows
    # (`record_external_usage`) carry the harness agent's routing reason
    # instead: "policy:default" | "owner_selected" | "policy:cross_vendor".
    # Empty for rows predating the field and for other no-provider failures.
    routing_reason = models.CharField(max_length=50, blank=True, default="")
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    context_report = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "created_at"]),
            models.Index(fields=["purpose", "created_at"]),
        ]

    def __str__(self):
        return f"{self.purpose} ({self.provider or 'no-provider'})"
