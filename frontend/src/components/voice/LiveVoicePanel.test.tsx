import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LiveVoicePanel } from "@/components/voice/LiveVoicePanel";
import type { ApiConversation, ApiVoiceLive } from "@/types/api";

const DEVICE = "44444444-4444-4444-8444-444444444444";
const WORKSPACE = "55555555-5555-4555-8555-555555555555";
const SHA = "a".repeat(64);

function live(overrides: Partial<ApiVoiceLive> = {}): ApiVoiceLive {
  return {
    always_on_enabled: true,
    max_far_per_hour: "0.2",
    max_frr: "0.10",
    min_household_hours: "24",
    storm_max_stt_per_minute: 8,
    any_model_permitted: false,
    nodes: [{ device_id: DEVICE, name: "MacBook", mute: null }],
    models: [],
    ...overrides,
  };
}

const PASSING_MODEL: ApiVoiceLive["models"][number] = {
  model_sha256: SHA,
  threshold: "0.5000",
  refractory_ms: 2000,
  permitted: true,
  reasons: [],
  household: {
    id: "h",
    kind: "household",
    hours: "24.000",
    false_accepts: 4,
    far_per_hour: "0.1667",
    positives: 0,
    false_rejects: 0,
    frr: null,
    threshold: "0.5000",
    refractory_ms: 2000,
    recorded_at: "2026-11-20T10:00:00Z",
  },
  positives: {
    id: "p",
    kind: "positives",
    hours: "0.000",
    false_accepts: 0,
    far_per_hour: null,
    positives: 50,
    false_rejects: 2,
    frr: "0.0400",
    threshold: "0.5000",
    refractory_ms: 2000,
    recorded_at: "2026-11-20T10:00:00Z",
  },
  negative_set: null,
};

function conversation(overrides: Partial<ApiConversation>): ApiConversation {
  return {
    id: "66666666-6666-4666-8666-666666666666",
    workspace: WORKSPACE,
    title: "Canlı ses — 2026-11-20 10:00",
    created_by: null,
    created_at: "2026-11-20T10:00:00Z",
    private: false,
    origin_channel: null,
    opened_via: "node_voice",
    ...overrides,
  };
}

function stub(
  first: ApiVoiceLive,
  { after, conversations = [] }: { after?: ApiVoiceLive; conversations?: ApiConversation[] } = {},
) {
  let reads = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/api/voice/live/") && init?.method === "DELETE") {
      return new Response(JSON.stringify({ device_id: DEVICE, released: true }), { status: 200 });
    }
    if (url === "/api/voice/live") {
      reads += 1;
      const body = reads > 1 && after ? after : first;
      return new Response(JSON.stringify(body), { status: 200 });
    }
    if (url.startsWith("/api/conversations")) {
      return new Response(JSON.stringify(conversations), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("LiveVoicePanel (Phase 11 D12/D15)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a closed gate with every reason", async () => {
    stub(
      live({
        always_on_enabled: false,
        models: [
          {
            ...PASSING_MODEL,
            permitted: false,
            reasons: ["disabled", "far_above_budget", "threshold_mismatch"],
          },
        ],
      }),
    );
    render(<LiveVoicePanel workspaceId={null} />);
    const verdict = await screen.findByTestId("live-voice-verdict");
    expect(verdict).toHaveAttribute("data-permitted", "false");
    expect(verdict).toHaveTextContent("Sürekli dinleme izinli değil");
    expect(screen.getByText(/Sunucu ayarı kapalı/)).toBeInTheDocument();
    expect(screen.getByTestId("live-voice-reasons")).toHaveTextContent(
      "sunucu ayarı kapalı · yanlış uyanma oranı bütçenin üstünde · ölçüm başka bir eşikte yapılmış",
    );
  });

  it.each([
    ["no_household_eval", "24 saatlik ev sesi değerlendirmesi yok"],
    ["no_positive_eval", "pozitif kayıt değerlendirmesi yok"],
    ["frr_above_budget", "kaçırma oranı bütçenin üstünde"],
    ["model_mismatch", "Mac'teki model ölçülen model değil"],
    ["storm_muted", "fırtına susturması etkin"],
  ] as const)("renders the %s reason", async (reason, text) => {
    stub(live({ models: [{ ...PASSING_MODEL, permitted: false, reasons: [reason] }] }));
    render(<LiveVoicePanel workspaceId={null} />);
    expect(await screen.findByTestId("live-voice-reasons")).toHaveTextContent(text);
  });

  it("shows a passing model as permitted with its measurements", async () => {
    stub(live({ any_model_permitted: true, models: [PASSING_MODEL] }));
    render(<LiveVoicePanel workspaceId={null} />);
    const verdict = await screen.findByTestId("live-voice-verdict");
    expect(verdict).toHaveAttribute("data-permitted", "true");
    expect(screen.getByText(/Ev sesi: 24.000 saat, saatte 0.1667/)).toBeInTheDocument();
    expect(screen.getByText(/50 kayıt, %4 kaçırma/)).toBeInTheDocument();
    expect(screen.getByText(/eşik 0.5000, bekleme 2000 ms/)).toBeInTheDocument();
  });

  it("says when nothing was measured yet", async () => {
    stub(live());
    render(<LiveVoicePanel workspaceId={null} />);
    expect(await screen.findByText(/Henüz kayıtlı değerlendirme yok/)).toBeInTheDocument();
  });

  it("has no release button when no node is muted", async () => {
    stub(live());
    render(<LiveVoicePanel workspaceId={null} />);
    expect(await screen.findByText("MacBook: canlı ses açık")).toBeInTheDocument();
    expect(screen.queryByTestId("live-voice-release")).not.toBeInTheDocument();
    expect(screen.queryByTestId("live-voice-storm")).not.toBeInTheDocument();
  });

  it("shows the storm banner and releases the mute through the BFF route", async () => {
    const muted = live({
      nodes: [
        {
          device_id: DEVICE,
          name: "MacBook",
          mute: { engaged_at: "2026-11-20T11:00:00Z", reason: "stt_storm" },
        },
      ],
    });
    const fetchMock = stub(muted, { after: live() });
    render(<LiveVoicePanel workspaceId={null} />);
    expect(await screen.findByTestId("live-voice-storm")).toHaveTextContent(
      "MacBook: canlı ses susturuldu",
    );
    await userEvent.click(screen.getByTestId("live-voice-release"));
    await waitFor(() =>
      expect(screen.queryByTestId("live-voice-release")).not.toBeInTheDocument(),
    );
    const release = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "DELETE",
    );
    expect(release?.[0]).toBe(`/api/voice/live/${DEVICE}/mute`);
    expect(screen.getByText("MacBook: canlı ses açık")).toBeInTheDocument();
  });

  it("lists only the live conversations of the workspace", async () => {
    stub(live(), {
      conversations: [
        conversation({ id: "1", title: "Canlı ses — sabah" }),
        conversation({ id: "2", title: "Web sohbeti", opened_via: "web" }),
        conversation({ id: "3", title: "Eski", opened_via: "" }),
      ],
    });
    render(<LiveVoicePanel workspaceId={WORKSPACE} />);
    const list = await screen.findByTestId("live-voice-conversations");
    expect(list).toHaveTextContent("Canlı ses — sabah");
    expect(list).not.toHaveTextContent("Web sohbeti");
    expect(list).not.toHaveTextContent("Eski");
  });

  it("shows an error when the state cannot be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ code: "owner_required" }), { status: 403 })),
    );
    render(<LiveVoicePanel workspaceId={null} />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
