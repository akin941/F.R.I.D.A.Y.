/**
 * The push-to-talk state machine (Phase 6 branch 3), pure:
 *
 *   idle ─press→ listening ─release→ transcribing ─transcript→ finishing
 *   finishing ─press (within VOICE_FINISH_MS)→ listening   (append, A7)
 *   finishing ─finish elapsed→ thinking ─first audio→ speaking ─done→ idle
 *   any ─abort→ idle · any ─fail→ error ─press→ listening
 *
 * `finishing` is the A7 finish window: the owner let go, the transcript is
 * in the buffer, and a press before the window elapses continues the same
 * turn instead of dispatching it. An interrupt word or a deterministic
 * voice route resolves the turn without the brain (`routed`).
 */

export type VoicePhase =
  | "idle"
  | "listening"
  | "transcribing"
  | "finishing"
  | "thinking"
  | "speaking"
  | "error";

export interface VoiceMachineState {
  phase: VoicePhase;
  /** Transcript pieces of the current turn not yet dispatched. */
  buffer: string;
  /** Error code (catalog code or a capture code) while in `error`. */
  error: string | null;
}

export type VoiceEvent =
  | { type: "press" }
  | { type: "release" }
  | { type: "transcript"; text: string }
  | { type: "finish_elapsed" }
  | { type: "routed" }
  | { type: "dispatch" }
  | { type: "speaking" }
  | { type: "done" }
  | { type: "abort" }
  | { type: "fail"; error: string };

export const INITIAL_VOICE_STATE: VoiceMachineState = { phase: "idle", buffer: "", error: null };

function join(buffer: string, text: string): string {
  const piece = text.trim();
  if (!piece) return buffer;
  return buffer ? `${buffer} ${piece}` : piece;
}

export function voiceReducer(state: VoiceMachineState, event: VoiceEvent): VoiceMachineState {
  switch (event.type) {
    case "press":
      // Pressing while Friday thinks or speaks stops the audio (half-duplex,
      // D7) and starts a new turn; in the finish window it continues one.
      if (state.phase === "listening" || state.phase === "transcribing") return state;
      if (state.phase === "finishing") return { ...state, phase: "listening" };
      return { phase: "listening", buffer: "", error: null };
    case "release":
      return state.phase === "listening" ? { ...state, phase: "transcribing" } : state;
    case "transcript": {
      if (state.phase !== "transcribing") return state;
      const buffer = join(state.buffer, event.text);
      // Nothing heard at all: back to idle, nothing sent.
      if (!buffer) return INITIAL_VOICE_STATE;
      return { ...state, phase: "finishing", buffer };
    }
    case "finish_elapsed":
      return state.phase === "finishing" ? { ...state, phase: "thinking" } : state;
    case "dispatch":
      return { ...state, phase: "thinking", buffer: "" };
    case "routed":
      return { phase: "idle", buffer: "", error: null };
    case "speaking":
      return state.phase === "thinking" || state.phase === "idle"
        ? { ...state, phase: "speaking" }
        : state;
    case "done":
      return state.phase === "thinking" || state.phase === "speaking" ? INITIAL_VOICE_STATE : state;
    case "abort":
      return INITIAL_VOICE_STATE;
    case "fail":
      return { phase: "error", buffer: "", error: event.error };
    default:
      return state;
  }
}
