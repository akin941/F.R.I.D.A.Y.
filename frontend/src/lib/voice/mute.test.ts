import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { MUTE_STORAGE_KEY, readMuteState, setVoiceMuted, useVoiceMute } from "@/lib/voice/mute";
import { TurnTimer, newTraceId } from "@/lib/voice/trace";

describe("voice mute flag", () => {
  afterEach(() => {
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
    vi.restoreAllMocks();
  });

  it("defaults to unmuted and round-trips", () => {
    expect(readMuteState()).toEqual({ muted: false, readable: true });
    setVoiceMuted(true);
    expect(window.localStorage.getItem(MUTE_STORAGE_KEY)).toBe("1");
    expect(readMuteState()).toEqual({ muted: true, readable: true });
    setVoiceMuted(false);
    expect(readMuteState().muted).toBe(false);
  });

  it("?mute=1 forces mute", () => {
    window.history.replaceState(null, "", "/voice?mute=1");
    expect(readMuteState()).toEqual({ muted: true, readable: true });
  });

  it("an unreadable state reads as muted (fail-closed)", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readMuteState()).toEqual({ muted: true, readable: false });
  });

  it("the hook follows the toggle", () => {
    const { result } = renderHook(() => useVoiceMute());
    expect(result.current.muted).toBe(false);
    act(() => setVoiceMuted(true));
    expect(result.current.muted).toBe(true);
  });
});

describe("trace", () => {
  it("trace ids are UUIDs", () => {
    expect(newTraceId()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it("derives integer stage timings from marks", () => {
    let now = 0;
    const timer = new TurnTimer(() => now);
    timer.mark("released");
    now = 400;
    timer.mark("transcribed");
    now = 1300;
    timer.mark("dispatched");
    now = 1800.4;
    timer.mark("firstSentence");
    now = 2100;
    timer.mark("playbackStart");
    timer.mark("lastRequest");
    expect(timer.timings()).toEqual({
      end_of_turn_ms: 900,
      llm_first_sentence_ms: 500,
      playback_start_ms: 2100,
      total_ms: 2100,
    });
    // Unknown stages are simply absent.
    expect(new TurnTimer(() => 0).timings()).toEqual({});
  });
});
