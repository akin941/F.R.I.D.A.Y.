import { describe, expect, it } from "vitest";
import { matchesRecentSpeech, normalizeSpeech, withinSelfTriggerWindow } from "@/lib/voice/self-trigger";

describe("self-trigger rule (A6)", () => {
  const recent = [{ text: "Hey Friday, terminali aç.", at: 0 }, { text: "Takvimin boş görünüyor.", at: 0 }];

  it("normalizes Turkish casing and punctuation", () => {
    expect(normalizeSpeech("  İSTANBUL'a, Gidiyorum!  ")).toBe("istanbul a gidiyorum");
  });

  it("matches an echo of a recent sentence, with or without the wake phrase", () => {
    expect(matchesRecentSpeech("hey friday terminali aç", recent)).toBe(true);
    expect(matchesRecentSpeech("terminali aç", recent)).toBe(true);
    expect(matchesRecentSpeech("takvimin boş görünüyor", recent)).toBe(true);
  });

  it("does not match a genuinely new request", () => {
    expect(matchesRecentSpeech("yarın saat onda diş hekimi", recent)).toBe(false);
    expect(matchesRecentSpeech("", recent)).toBe(false);
    expect(matchesRecentSpeech("hey friday", recent)).toBe(false);
  });

  it("applies only within 1.5 s after playback ended", () => {
    expect(withinSelfTriggerWindow(10_000, 9_000)).toBe(true);
    expect(withinSelfTriggerWindow(10_000, 8_400)).toBe(false);
    expect(withinSelfTriggerWindow(10_000, null)).toBe(false);
    expect(withinSelfTriggerWindow(9_000, 10_000)).toBe(false);
  });
});
