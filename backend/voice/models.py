from django.db import models

from assistant.models import Message
from core.db import WorkspaceScopedModel
from core.models import TimestampedModel

# Every stage a trace row may time (Phase 6 A5's latency table plus the
# end-to-end figure). Server-measured: `stt_ms` (the transcription call's
# gateway latency) and `tts_first_ms` (the turn's first speech call).
# Client-reported with the last speech request: the rest.
SERVER_STAGES = ("stt_ms", "tts_first_ms")
# Phase 11 D14 adds `wake_to_listen_ms` (wake detection -> capture start)
# and `interrupted_at_ms` (barge-in, ms after playback start), reported by
# the node's voice child; the browser may report them too.
CLIENT_STAGES = (
    "end_of_turn_ms",
    "llm_first_sentence_ms",
    "playback_start_ms",
    "total_ms",
    "wake_to_listen_ms",
    "interrupted_at_ms",
)
STAGES = SERVER_STAGES + CLIENT_STAGES
# Where the turn was spoken (Phase 11 D14). Set by the server from the
# request's channel (`node` for CHANNEL_NODE_VOICE, `web` otherwise), never
# read from the client's `timings` body.
SOURCE_WEB = "web"
SOURCE_NODE = "node"
SOURCES = ((SOURCE_WEB, "Web"), (SOURCE_NODE, "Node"))
# One stage is never longer than this (10 min); a bigger value is garbage.
MAX_STAGE_MS = 600_000


class VoiceTurnTrace(WorkspaceScopedModel, TimestampedModel):
    """Stage timings of one spoken turn (Phase 6 A5; amends D2: no voice
    table holds audio or text). Keyed by the client's trace id
    (`X-Friday-Trace`). Holds timings, the conversation and the input mode
    ONLY -- never audio, a transcript or reply text, and by construction no
    free-text column at all (a field allow-list test pins the shape).
    Pruned after `VOICE_TRACE_RETENTION_DAYS` (`voice.tasks`)."""

    trace_id = models.UUIDField()
    conversation = models.ForeignKey(
        "assistant.Conversation", on_delete=models.CASCADE, related_name="voice_traces"
    )
    input_mode = models.CharField(
        max_length=10, choices=Message.InputMode.choices, default=Message.InputMode.VOICE
    )
    source = models.CharField(max_length=4, choices=SOURCES, default=SOURCE_WEB)
    stt_ms = models.PositiveIntegerField(null=True, blank=True)
    tts_first_ms = models.PositiveIntegerField(null=True, blank=True)
    end_of_turn_ms = models.PositiveIntegerField(null=True, blank=True)
    llm_first_sentence_ms = models.PositiveIntegerField(null=True, blank=True)
    playback_start_ms = models.PositiveIntegerField(null=True, blank=True)
    total_ms = models.PositiveIntegerField(null=True, blank=True)
    wake_to_listen_ms = models.PositiveIntegerField(null=True, blank=True)
    interrupted_at_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "trace_id"], name="voice_trace_unique_per_workspace"
            )
        ]
        indexes = [models.Index(fields=["created_at"])]

    def __str__(self):
        return f"VoiceTurnTrace {self.trace_id}"
