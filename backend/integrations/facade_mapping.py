"""Stateless client -> server conversation mapping for the OpenAI-compatible
facade (Phase 4 branch 6, brief §6 "Stateless -> conversation mapping").

An OpenAI-compatible client (Open WebUI, ...) resends the whole history on
every request. Jarvis never trusts it: only the client's *user* turns are
read, and only as hashes to find the server-side conversation the new turn
belongs to; the context the model sees is always the server's own history
(`assistant.context.build_context`). Client `system`/`assistant`/`tool`
messages are ignored and never persisted -- a client cannot plant fake
assistant turns into memory extraction.

Per request (the new turn is the last user message; "prior" are the user
turns before it):

- a pinned conversation (`X-Jarvis-Conversation-Id`) gets the turn as is --
  bindings are not consulted or changed;
- else the bindings of this token with the same anchor (token id + first
  user message) are candidates. The most recent one whose recorded turns are
  a prefix of the client's prior turns gets the turn ("append": the client
  just continued, possibly after turns Jarvis never saw);
- else, when some candidate shares at least one prior turn, the client
  regenerated or edited a turn: **fork** -- a new conversation seeded with
  copies of the best candidate's server-side messages up to the divergence
  (`assistant.services.fork_conversation`);
- else a new conversation.

Every resolution runs under a row lock on the token, so concurrent requests
of one token resolve one after another. The binding records the client's
full user-turn list, so the next request of the same chat matches again.
"""

import hashlib
import uuid
from dataclasses import dataclass

from django.db import transaction

from accounts.models import PersonalAccessToken
from assistant.models import Conversation, Message
from assistant.services import create_conversation, fork_conversation
from core.channels import CHANNEL_OPENAI
from integrations.models import FacadeConversation

# A new conversation's title: the start of its first user message.
_TITLE_MAX_CHARS = 60


def turn_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def anchor_hash(token_id, first_user_text):
    return hashlib.sha256(f"{token_id}\x1f{first_user_text}".encode()).hexdigest()


def _title_from(text):
    line = " ".join(text.split())
    if len(line) <= _TITLE_MAX_CHARS:
        return line
    return line[: _TITLE_MAX_CHARS - 1] + "…"


def _common_prefix_length(left, right):
    length = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        length += 1
    return length


@dataclass
class Resolution:
    conversation: Conversation
    # The id the new user Message must be created with (recorded on the
    # binding before the message exists; the gateway calls run in between).
    user_message_id: uuid.UUID
    created: bool = False
    forked_from: Conversation | None = None


def resolve_conversation(*, actor, token, workspace, user_turns, pinned=None):
    """Find or create the conversation the client's newest user turn
    (`user_turns[-1]`) belongs to. `token` is the request's own
    PersonalAccessToken, `workspace` its bound workspace (already resolved
    through `scoped_for_actor`), `pinned` an already-scoped Conversation or
    None. Returns a `Resolution`."""
    user_message_id = uuid.uuid4()
    if pinned is not None:
        return Resolution(conversation=pinned, user_message_id=user_message_id)

    hashes = [turn_hash(text) for text in user_turns]
    prior = hashes[:-1]
    anchor = anchor_hash(token.pk, user_turns[0])

    with transaction.atomic():
        # Serializes every resolution of this token (row lock).
        PersonalAccessToken.objects.select_for_update().filter(pk=token.pk).first()
        candidates = list(
            FacadeConversation.objects.for_actor(actor, workspace=workspace)
            .filter(token=token, anchor_hash=anchor)
            .select_related("conversation")
            .order_by("-updated_at", "-id")
        )

        continued = [
            candidate
            for candidate in candidates
            if prior[: len(candidate.user_turn_hashes)] == candidate.user_turn_hashes
        ]
        if continued:
            # The longest recorded history wins; the most recent on a tie
            # (e.g. a fork and its source after identical turns).
            binding = max(continued, key=lambda c: len(c.user_turn_hashes))
            known = len(binding.user_turn_hashes)
            binding.user_turn_hashes = hashes
            binding.user_turn_message_ids = [
                *binding.user_turn_message_ids,
                *([None] * (len(prior) - known)),
                str(user_message_id),
            ]
            binding.save(update_fields=["user_turn_hashes", "user_turn_message_ids", "updated_at"])
            return Resolution(conversation=binding.conversation, user_message_id=user_message_id)

        best, shared = None, 0
        for candidate in candidates:
            length = _common_prefix_length(candidate.user_turn_hashes, prior)
            if length > shared:
                best, shared = candidate, length

        forked_from = None
        seeded_ids = []
        if best is not None:
            # Copy the source up to its first server-side user message at or
            # after the divergence; everything before it answers the shared
            # turns. (A recorded id whose request failed before the message
            # was written does not exist -- skip it.)
            existing = {
                str(mid)
                for mid in Message.objects.filter(
                    conversation=best.conversation,
                    id__in=[mid for mid in best.user_turn_message_ids if mid is not None],
                ).values_list("id", flat=True)
            }
            cut_before_id = next(
                (mid for mid in best.user_turn_message_ids[shared:] if mid in existing), None
            )
            conversation, id_map = fork_conversation(
                source=best.conversation,
                actor=actor,
                cut_before_id=cut_before_id,
                channel=CHANNEL_OPENAI,
            )
            seeded_ids = [
                id_map.get(mid) if mid is not None else None
                for mid in best.user_turn_message_ids[:shared]
            ]
            forked_from = best.conversation
        else:
            conversation = create_conversation(
                workspace=workspace,
                actor=actor,
                title=_title_from(user_turns[0]),
                channel=CHANNEL_OPENAI,
            )

        FacadeConversation.objects.create(
            workspace=workspace,
            token=token,
            conversation=conversation,
            anchor_hash=anchor,
            user_turn_hashes=hashes,
            user_turn_message_ids=[
                *seeded_ids,
                *([None] * (len(prior) - len(seeded_ids))),
                str(user_message_id),
            ],
        )
    return Resolution(
        conversation=conversation,
        user_message_id=user_message_id,
        created=True,
        forked_from=forked_from,
    )
