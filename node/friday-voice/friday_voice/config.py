"""Every child-side tunable, in one module (Phase 11 brief D8).

The child has no Django settings and no environment of its own: the host
hands it only the `friday-voice/v1` frames. Every number the child runs at
therefore lives here, and changing one is a code change in
`node/friday-voice`, reviewed like any other. A change to WAKE_THRESHOLD or
WAKE_REFRACTORY_MS re-closes the always-on gate (D11) until the model is
re-measured at the new values: `WakeGate`, the `wake-eval` harness
defaults and the voice-policy request all read them from here, never from
a copy (tests/test_config.py).

Server-side numbers (token TTL, storm backstop, FAR/FRR budgets, the
always-on switch) stay Django settings.

Probabilities and scores are integers in thousandths (0..1000), like the
ports: no float ever reaches the protocol.
"""

SAMPLE_RATE = 16_000
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000  # 16 kHz mono PCM16: 32 bytes per ms

# --- wake (D8, D11) ---
# The Phase 6 browser value (frontend/src/lib/voice/config.ts
# VOICE_WAKE_DEFAULT_THRESHOLD = 0.5), in thousandths.
WAKE_THRESHOLD = 500
WAKE_REFRACTORY_MS = 2_000
WAKE_NO_SPEECH_S = 5
RING_BUFFER_S = 2  # pre-wake audio lives only here (ADR-011 decision 2)

# --- turn capture (D8) ---
VAD_SPEECH_THRESHOLD = 500  # the browser's VOICE_VAD_THRESHOLD = 0.5
TURN_TRAILING_SILENCE_MS = 200  # then smart-turn v3 scores the turn
TURN_HARD_SILENCE_MS = 1_500  # an "incomplete" turn still ends here
UTTERANCE_MAX_S = 30  # ADR-011 upload cap (VOICE_MAX_UTTERANCE_SECONDS)
PTT_MIN_MS = 300  # a shorter push-to-talk press is a slip: nothing is uploaded

# --- barge-in (D9, branch 4) ---
BARGE_IN_VAD = 700
BARGE_IN_MIN_MS = 250
BARGE_IN_AEC_GUARD_MS = 300
BARGE_IN_PREROLL_MS = 300

# --- self-trigger (D10, branch 4) ---
SELF_TRIGGER_MS = 1_500

# --- sessions and storms (D12, branch 5) ---
SILENCE_CLOSE_S = 30
SESSION_MAX_MINUTES = 15
WAKE_STORM_MAX = 3
WAKE_STORM_WINDOW_S = 60
POLICY_POLL_S = 600  # the always-on policy is re-read every 10 minutes (D11)
POLICY_RETRY_S = 30  # after a failed read (disarmed meanwhile): retried this often

# --- replies (D8) ---
TTS_PREFETCH = 1  # one sentence synthesized ahead of the one playing (Phase 6 D7)
PLAY_CHUNK_MS = 200  # one `play` frame: 6 400 bytes, far below the 64 KiB cap
DRAFT_SENTENCE = "Kart ekranda."  # Phase 6 D8: a draft is on the screen, never "yapıldı"
ERROR_HOLD_MS = 2_000  # how long `error` shows after a refused or failed turn

# --- network (D8) ---
HTTP_CONNECT_TIMEOUT_S = 5
HTTP_READ_TIMEOUT_S = 30
STT_RETRIES = 1  # network errors only; a chat turn is never sent twice
TTS_RETRIES = 1

# --- replay (D14) ---
REPLAY_REGRESSION_PCT = 20  # pipeline overhead p50 may grow at most this much


def ms_to_bytes(ms: int) -> int:
    return int(ms) * BYTES_PER_MS


def bytes_to_ms(size: int) -> int:
    return int(size) // BYTES_PER_MS
