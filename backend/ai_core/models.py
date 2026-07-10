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

    class Meta:
        ordering = ["priority", "name"]

    def __str__(self):
        return f"{self.name} ({self.model_id})"


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
