import { describe, expect, it } from "vitest";
import {
  INITIAL_VOICE_STATE,
  voiceReducer,
  type VoiceEvent,
  type VoiceMachineState,
} from "@/lib/voice/voice-machine";

function run(events: VoiceEvent[], from: VoiceMachineState = INITIAL_VOICE_STATE) {
  return events.reduce(voiceReducer, from);
}

describe("voiceReducer", () => {
  it("walks the happy path idle -> listening -> transcribing -> finishing -> thinking -> speaking -> idle", () => {
    const phases: string[] = [];
    let state = INITIAL_VOICE_STATE;
    for (const event of [
      { type: "press" },
      { type: "release" },
      { type: "transcript", text: "Merhaba" },
      { type: "finish_elapsed" },
      { type: "speaking" },
      { type: "done" },
    ] as VoiceEvent[]) {
      state = voiceReducer(state, event);
      phases.push(state.phase);
    }
    expect(phases).toEqual(["listening", "transcribing", "finishing", "thinking", "speaking", "idle"]);
  });

  it("a press inside the finish window continues the turn and appends (A7)", () => {
    const state = run([
      { type: "press" },
      { type: "release" },
      { type: "transcript", text: "Yarın saat onda" },
      { type: "press" },
      { type: "release" },
      { type: "transcript", text: "diş hekimi" },
    ]);
    expect(state.phase).toBe("finishing");
    expect(state.buffer).toBe("Yarın saat onda diş hekimi");
  });

  it("an empty transcript goes back to idle with nothing buffered", () => {
    expect(run([{ type: "press" }, { type: "release" }, { type: "transcript", text: "  " }])).toEqual(
      INITIAL_VOICE_STATE,
    );
  });

  it("abort mid-transcription returns to idle and drops the buffer", () => {
    const state = run([{ type: "press" }, { type: "release" }, { type: "abort" }]);
    expect(state).toEqual(INITIAL_VOICE_STATE);
    // A transcript arriving after the abort is ignored.
    expect(voiceReducer(state, { type: "transcript", text: "geç" })).toEqual(INITIAL_VOICE_STATE);
  });

  it("abort mid-playback returns to idle", () => {
    const speaking = run([
      { type: "press" },
      { type: "release" },
      { type: "transcript", text: "Merhaba" },
      { type: "finish_elapsed" },
      { type: "speaking" },
    ]);
    expect(speaking.phase).toBe("speaking");
    expect(voiceReducer(speaking, { type: "abort" })).toEqual(INITIAL_VOICE_STATE);
  });

  it("a press while speaking starts a new turn (half-duplex)", () => {
    const speaking: VoiceMachineState = { phase: "speaking", buffer: "", error: null };
    expect(voiceReducer(speaking, { type: "press" }).phase).toBe("listening");
  });

  it("errors are recoverable by pressing again", () => {
    const error = run([{ type: "press" }, { type: "fail", error: "voice.budget_exceeded" }]);
    expect(error).toEqual({ phase: "error", buffer: "", error: "voice.budget_exceeded" });
    expect(voiceReducer(error, { type: "press" })).toEqual({ phase: "listening", buffer: "", error: null });
  });

  it("ignores out-of-order events", () => {
    expect(voiceReducer(INITIAL_VOICE_STATE, { type: "release" })).toBe(INITIAL_VOICE_STATE);
    expect(voiceReducer(INITIAL_VOICE_STATE, { type: "finish_elapsed" })).toBe(INITIAL_VOICE_STATE);
    expect(voiceReducer(INITIAL_VOICE_STATE, { type: "done" })).toBe(INITIAL_VOICE_STATE);
    const listening = run([{ type: "press" }]);
    expect(voiceReducer(listening, { type: "press" })).toBe(listening);
  });

  it("a routed utterance (voice route) resolves to idle without dispatch", () => {
    const state = run([
      { type: "press" },
      { type: "release" },
      { type: "transcript", text: "bu ne kadar tuttu" },
      { type: "routed" },
    ]);
    expect(state).toEqual(INITIAL_VOICE_STATE);
  });
});
