from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel


class TelegramLinkCode(WorkspaceScopedModel, TimestampedModel):
    """A short-lived, one-time code minted from the web app (JWT-only,
    §4) that a user pastes into Telegram as `/link <code>` to bind a
    private chat to their (user, workspace). Consumption is atomic
    (integrations.tasks._consume_link_code) so a redelivered webhook
    update can never consume the same code twice."""

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    code = models.CharField(max_length=32, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"link code for {self.user_id} ({self.workspace_id})"


class LinkedTelegramChat(WorkspaceScopedModel, TimestampedModel):
    """One Telegram private chat, bound to exactly one (user, workspace)
    and one Conversation for the lifetime of the link (no in-bot "new
    conversation" command, MVP 9 §4). `chat_id` is independently unique --
    a physical Telegram chat can only ever back one row -- on top of the
    (user, workspace) UniqueConstraint below; relinking (from a new phone,
    or the same phone to a different workspace) replaces whichever row(s)
    would otherwise collide rather than erroring (round-4 fix, see
    integrations.tasks._consume_link_code)."""

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    chat_id = models.CharField(max_length=64, unique=True)
    conversation = models.ForeignKey("assistant.Conversation", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "workspace"], name="unique_telegram_link_per_user_workspace"
            )
        ]

    def __str__(self):
        return f"{self.chat_id} -> {self.user_id} ({self.workspace_id})"


class FacadeConversation(WorkspaceScopedModel, TimestampedModel):
    """Binds a stateless OpenAI-compatible client's chat to a server-side
    Conversation (Phase 4 branch 6, `integrations.facade_mapping`).

    Clients resend the whole history on every request; Jarvis's context
    comes from its own history, so each request is matched to a binding of
    the same token whose `anchor_hash` (sha256 over the token id and the
    first user message) matches and whose recorded user turns are a prefix
    of the client's prior user turns. `user_turn_hashes` are the sha256s of
    the client's user turns in order; `user_turn_message_ids` is the
    parallel list of the server-side user Message each turn became (None
    for a turn Jarvis never saw -- history the client brought along, or a
    turn answered by another model). The workspace is always the token's."""

    token = models.ForeignKey(
        "accounts.PersonalAccessToken", on_delete=models.CASCADE, related_name="+"
    )
    conversation = models.ForeignKey(
        "assistant.Conversation", on_delete=models.CASCADE, related_name="+"
    )
    anchor_hash = models.CharField(max_length=64)
    user_turn_hashes = models.JSONField(default=list)
    user_turn_message_ids = models.JSONField(default=list)

    class Meta:
        indexes = [models.Index(fields=["token", "anchor_hash"])]

    def __str__(self):
        return f"facade binding {self.id} -> {self.conversation_id}"
