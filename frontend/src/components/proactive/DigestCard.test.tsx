import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DigestCard, type DigestCardData } from "@/components/proactive/DigestCard";
import { digestSentences } from "@/lib/proactive/digest-speech";
import { MUTE_STORAGE_KEY } from "@/lib/voice/mute";
import { Speaker } from "@/lib/voice/playback";
import { fakeAudio } from "@/lib/voice/playback.test";

// Phase 12 D7: "Dinle" and speak_on_open with the fake speech client --
// one /voice/speech call per segment, nothing when muted, and the
// microphone is never opened (the Phase 6 mute spec, extended).

const TEXT =
  "Günaydın, bugün seni bekleyen birkaç şey var. İki onay seni bekliyor ve saat 10:00'da Sprint planı var. Ayrıntılar Bugün sayfasında.";

const DIGEST: DigestCardData = {
  id: "d1",
  created_at: "2026-11-24T04:45:00Z",
  spoken_text: TEXT,
  speak_on_open: true,
  spoken: false,
  sections: [
    {
      section: "approvals",
      title: "Onaylar",
      entries: [
        {
          probe: "approvals.pending",
          name: "Bekleyen onay",
          count: 2,
          link: "/approvals",
          items: [{ label: "Onay: vault_export", link: "/approvals/x" }],
        },
      ],
    },
  ],
};

function speechStub() {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    if (input === "/api/conversations") {
      return new Response(JSON.stringify({ id: "c1", workspace: "w1" }), { status: 201 });
    }
    if (input === "/api/voice/speech") {
      calls.push(JSON.parse(String(init?.body)).text);
      return new Response(new ArrayBuffer(8), { headers: { "content-type": "audio/wav" } });
    }
    return new Response(JSON.stringify({ changed: true }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function deps(ack = vi.fn(async () => ({}) as never)) {
  const audio = fakeAudio();
  return {
    ack,
    audio,
    createSpeaker: (opts: ConstructorParameters<typeof Speaker>[0]) =>
      new Speaker({ ...opts, createAudioContext: () => audio.context }),
  };
}

const getUserMedia = vi.fn();

beforeEach(() => {
  window.localStorage.clear();
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  getUserMedia.mockReset();
});

describe("DigestCard", () => {
  it("Dinle sends one speech request per segment through the speech endpoint", async () => {
    const { calls } = speechStub();
    const d = deps();
    render(<DigestCard digest={DIGEST} workspaceId="w1" playerDeps={d} />);
    await userEvent.click(screen.getByTestId("digest-listen"));
    const expected = digestSentences(TEXT);
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(Math.min(2, expected.length)));
    // Let every queued sentence play through.
    for (let i = 0; i < expected.length; i += 1) {
      await waitFor(() => expect(d.audio.sources.length).toBeGreaterThan(i));
      d.audio.sources[i]!.end();
    }
    await waitFor(() => expect(calls).toEqual(expected));
    expect(d.ack).toHaveBeenCalledWith("d1", "spoken");
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("does nothing when muted", async () => {
    window.localStorage.setItem(MUTE_STORAGE_KEY, "1");
    const { fetchMock } = speechStub();
    const d = deps();
    render(<DigestCard digest={DIGEST} workspaceId="w1" playerDeps={d} />);
    await userEvent.click(screen.getByTestId("digest-listen"));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(d.ack).not.toHaveBeenCalled();
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("speaks once on open in the HUD when the schedule asks for it", async () => {
    const { calls } = speechStub();
    const d = deps();
    const { rerender } = render(<DigestCard digest={DIGEST} workspaceId="w1" variant="hud" autoSpeak playerDeps={d} />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    const first = calls.length;
    rerender(<DigestCard digest={{ ...DIGEST }} workspaceId="w1" variant="hud" autoSpeak playerDeps={d} />);
    // A second render (or a second HUD open on this device) never repeats it.
    render(<DigestCard digest={DIGEST} workspaceId="w1" variant="hud" autoSpeak playerDeps={deps()} />);
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(calls.length).toBe(first);
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("never speaks on open when muted, when not asked, or when already spoken", async () => {
    const { fetchMock } = speechStub();
    window.localStorage.setItem(MUTE_STORAGE_KEY, "1");
    render(<DigestCard digest={DIGEST} workspaceId="w1" variant="hud" autoSpeak playerDeps={deps()} />);
    window.localStorage.removeItem(MUTE_STORAGE_KEY);
    render(
      <DigestCard
        digest={{ ...DIGEST, id: "d2", speak_on_open: false }}
        workspaceId="w1"
        variant="hud"
        autoSpeak
        playerDeps={deps()}
      />,
    );
    render(
      <DigestCard digest={{ ...DIGEST, id: "d3", spoken: true }} workspaceId="w1" variant="hud" autoSpeak playerDeps={deps()} />,
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("acknowledges `opened` once on /today and shows the facts on Ayrıntı", async () => {
    speechStub();
    const ack = vi.fn(async () => ({}) as never);
    const { rerender } = render(<DigestCard digest={DIGEST} workspaceId="w1" ackOpened playerDeps={deps(ack)} />);
    rerender(<DigestCard digest={{ ...DIGEST }} workspaceId="w1" ackOpened playerDeps={deps(ack)} />);
    expect(ack).toHaveBeenCalledTimes(1);
    expect(ack).toHaveBeenCalledWith("d1", "opened");
    await userEvent.click(screen.getByRole("button", { name: "Ayrıntı" }));
    expect(screen.getByTestId("digest-details")).toHaveTextContent("Bekleyen onay: 2");
    expect(screen.getByTestId("digest-details")).toHaveTextContent("Onay: vault_export");
  });

  it("shows an empty state without a digest", () => {
    render(<DigestCard digest={null} workspaceId="w1" />);
    expect(screen.getByTestId("digest-card-empty")).toHaveTextContent("Bugün için brifing yok.");
  });
});
