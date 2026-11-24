import { afterEach, describe, expect, it, vi } from "vitest";
import {
  digestSentences,
  markSpokenOnOpen,
  shouldSpeakOnOpen,
  speakDigest,
  type DigestSpeaker,
} from "@/lib/proactive/digest-speech";

const TEXT =
  "Günaydın, bugün seni bekleyen birkaç şey var. İki onay seni bekliyor ve saat 10:00'da Sprint planı var. Ayrıntılar Bugün sayfasında.";

const unmuted = () => ({ muted: false, readable: true });
const muted = () => ({ muted: true, readable: true });
const unreadable = () => ({ muted: true, readable: false });

function recorder(): DigestSpeaker & { spoken: string[]; ended: number } {
  const state = {
    spoken: [] as string[],
    ended: 0,
    speak(text: string) {
      state.spoken.push(text);
      return true;
    },
    end() {
      state.ended += 1;
    },
    stop: vi.fn(),
  };
  return state;
}

describe("digest speech (Phase 12 D7)", () => {
  afterEach(() => window.localStorage.clear());

  it("splits the digest into speakable sentences", () => {
    const sentences = digestSentences(TEXT);
    expect(sentences.length).toBeGreaterThanOrEqual(2);
    expect(sentences.join(" ")).toContain("Sprint planı");
  });

  it("queues one speak call per segment, then ends", () => {
    const speaker = recorder();
    const queued = speakDigest(TEXT, speaker, unmuted);
    expect(queued).toBe(digestSentences(TEXT).length);
    expect(speaker.spoken).toEqual(digestSentences(TEXT));
    expect(speaker.ended).toBe(1);
  });

  it("does nothing at all when muted or when the mute state is unreadable", () => {
    for (const readMute of [muted, unreadable]) {
      const speaker = recorder();
      expect(speakDigest(TEXT, speaker, readMute)).toBe(0);
      expect(speaker.spoken).toEqual([]);
      expect(speaker.ended).toBe(0);
    }
  });

  it("speaks on open once per digest, only when asked and not yet spoken", () => {
    const digest = { id: "d1", speak_on_open: true, spoken: false };
    expect(shouldSpeakOnOpen(digest, unmuted)).toBe(true);
    markSpokenOnOpen("d1");
    expect(shouldSpeakOnOpen(digest, unmuted)).toBe(false);
    expect(shouldSpeakOnOpen({ ...digest, id: "d2" }, unmuted)).toBe(true);
    expect(shouldSpeakOnOpen({ ...digest, id: "d2", speak_on_open: false }, unmuted)).toBe(false);
    expect(shouldSpeakOnOpen({ ...digest, id: "d2", spoken: true }, unmuted)).toBe(false);
    expect(shouldSpeakOnOpen({ ...digest, id: "d2" }, muted)).toBe(false);
    expect(shouldSpeakOnOpen(null, unmuted)).toBe(false);
  });
});
