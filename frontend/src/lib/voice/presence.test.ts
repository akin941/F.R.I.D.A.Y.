import { afterEach, describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  clearVoicePresence,
  IDLE_PRESENCE,
  publishVoicePresence,
  readVoiceLevel,
  useVoicePresence,
} from "@/lib/voice/presence";

afterEach(() => clearVoicePresence());

describe("voice presence (A8 mirror store)", () => {
  it("publishes to subscribers and clears back to idle", () => {
    const { result } = renderHook(() => useVoicePresence());
    expect(result.current).toEqual(IDLE_PRESENCE);
    act(() => publishVoicePresence({ phase: "listening", heard: "merhaba", reply: "", error: null }, () => 0.4));
    expect(result.current).toMatchObject({ phase: "listening", heard: "merhaba", active: true });
    expect(readVoiceLevel()).toBeCloseTo(0.4);
    act(() => clearVoicePresence());
    expect(result.current).toEqual(IDLE_PRESENCE);
    expect(readVoiceLevel()).toBe(0);
  });

  it("clamps the level and survives a throwing source", () => {
    publishVoicePresence({ phase: "speaking", heard: "", reply: "", error: null }, () => 7);
    expect(readVoiceLevel()).toBe(1);
    publishVoicePresence({ phase: "speaking", heard: "", reply: "", error: null }, () => {
      throw new Error("analyser gone");
    });
    expect(readVoiceLevel()).toBe(0);
  });
});
