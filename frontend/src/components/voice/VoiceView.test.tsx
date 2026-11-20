import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VoiceView } from "@/components/voice/VoiceView";
import type { CaptureSession } from "@/lib/voice/capture";
import { CaptureError } from "@/lib/voice/capture";
import { MUTE_STORAGE_KEY } from "@/lib/voice/mute";
import { readVoicePresence } from "@/lib/voice/presence";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: { id: "w1", name: "Alice", type: "personal", role: "owner" } }),
}));

const CONVERSATION = {
  id: "c1",
  workspace: "w1",
  title: "Sesli",
  created_by: null,
  created_at: "2026-07-16T00:00:00Z",
};

const DONE = 'data: {"type":"done","message_id":"a1","memories_used":[],"chunks_used":[]}\n\n';

function sseResponse(frames: string[]) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

function wavResponse() {
  return new Response(new Uint8Array([0x52, 0x49, 0x46, 0x46]), {
    status: 200,
    headers: { "Content-Type": "audio/wav" },
  });
}

/** A fake AudioContext: every sentence "plays" and ends on the next tick. */
function fakeAudioContext(): AudioContext {
  return {
    state: "running",
    destination: {},
    createAnalyser: () => ({ fftSize: 32, connect: vi.fn(), getFloatTimeDomainData: vi.fn() }),
    decodeAudioData: vi.fn(async () => ({})),
    createBufferSource: () => {
      const node = {
        buffer: null,
        onended: null as (() => void) | null,
        connect: vi.fn(),
        start: vi.fn(() => setTimeout(() => node.onended?.(), 0)),
        stop: vi.fn(),
      };
      return node;
    },
    resume: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
  } as unknown as AudioContext;
}

function fakeCapture() {
  const session: CaptureSession = {
    samples: 16_000,
    stop: vi.fn(async () => new Uint8Array(new ArrayBuffer(46))),
    cancel: vi.fn(),
  };
  return { session, startCapture: vi.fn(async () => session) };
}

type Route = (init: RequestInit | undefined) => Response | Promise<Response>;

function stubFetch(routes: Record<string, Route>) {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    const route = routes[url.split("?")[0]!];
    if (!route) throw new Error(`Unexpected fetch: ${url}`);
    return route(init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function header(init: RequestInit | undefined, name: string): string | undefined {
  return (init?.headers as Record<string, string> | undefined)?.[name];
}

async function holdToTalk() {
  const mic = screen.getByTestId("voice-mic");
  const now = vi.spyOn(performance, "now");
  now.mockReturnValue(1_000);
  await act(async () => {
    fireEvent.pointerDown(mic, { button: 0 });
  });
  now.mockReturnValue(2_000);
  await act(async () => {
    fireEvent.pointerUp(mic, { button: 0 });
  });
  now.mockRestore();
}

const baseDeps = (capture = fakeCapture()) => ({
  startCapture: capture.startCapture,
  createAudioContext: fakeAudioContext,
  finishMs: 0,
});

describe("VoiceView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("streams a typed reply and returns the orb to idle", async () => {
    stubFetch({
      "/api/conversations": () => new Response(JSON.stringify(CONVERSATION), { status: 201 }),
      "/api/conversations/c1/messages": () =>
        sseResponse(['data: {"type":"chunk","text":"Merhaba"}\n\n', DONE]),
    });
    render(<VoiceView depsOverride={baseDeps()} />);
    expect(screen.getByRole("img", { name: "Friday boşta" })).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("Friday'ye yaz…"), "Merhaba Friday");
    await userEvent.click(screen.getByRole("button", { name: "Gönder" }));
    expect(await screen.findByText("Merhaba")).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "Friday boşta" })).toBeInTheDocument();
  });

  it("shows an error banner on a stream error", async () => {
    stubFetch({
      "/api/conversations": () => new Response(JSON.stringify(CONVERSATION), { status: 201 }),
      "/api/conversations/c1/messages": () =>
        sseResponse(['data: {"type":"error","error_type":"no_provider"}\n\n']),
    });
    render(<VoiceView depsOverride={baseDeps()} />);
    await userEvent.type(screen.getByPlaceholderText("Friday'ye yaz…"), "Merhaba Friday");
    await userEvent.click(screen.getByRole("button", { name: "Gönder" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Şu anda yapılandırılmış bir yapay zeka sağlayıcısı yok. Lütfen daha sonra tekrar deneyin.",
    );
    expect(screen.getByRole("img", { name: "Friday: bir sorun var" })).toBeInTheDocument();
  });

  it("push-to-talk: transcript sent with input_mode=voice and the reply spoken per sentence", async () => {
    const calls = stubFetch({
      "/api/conversations": () => new Response(JSON.stringify(CONVERSATION), { status: 201 }),
      "/api/voice/transcriptions": () =>
        new Response(JSON.stringify({ text: "Yarın hava nasıl?", audio_seconds: 1, language: "tr" })),
      "/api/conversations/c1/messages": () =>
        sseResponse([
          'data: {"type":"chunk","text":"Yarın hava güneşli olacak. "}\n\n',
          'data: {"type":"chunk","text":"Akşama doğru biraz serinler."}\n\n',
          DONE,
        ]),
      "/api/voice/speech": () => wavResponse(),
    });
    render(<VoiceView depsOverride={baseDeps()} />);
    await holdToTalk();
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/voice/speech")).toHaveLength(2));
    const message = calls.find((c) => c.url === "/api/conversations/c1/messages")!;
    const body = JSON.parse(String(message.init!.body));
    expect(body).toMatchObject({ content: "Yarın hava nasıl?", input_mode: "voice" });
    // One trace id for the transcription, the message and the speech.
    const trace = header(message.init, "X-Friday-Trace");
    expect(trace).toMatch(/^[0-9a-f-]{36}$/);
    expect(header(calls.find((c) => c.url === "/api/voice/transcriptions")!.init, "X-Friday-Trace")).toBe(trace);
    const speech = calls.filter((c) => c.url === "/api/voice/speech");
    expect(speech.map((c) => JSON.parse(String(c.init!.body)).text)).toEqual([
      "Yarın hava güneşli olacak.",
      "Akşama doğru biraz serinler.",
    ]);
    expect(speech.every((c) => header(c.init, "X-Friday-Trace") === trace)).toBe(true);
    expect(await screen.findByTestId("voice-reply")).toHaveTextContent("Akşama doğru biraz serinler.");
    await waitFor(() => expect(screen.getByRole("img", { name: "Friday boşta" })).toBeInTheDocument());
  });

  it("a voice turn's task draft renders as a card and nothing is auto-confirmed", async () => {
    const calls = stubFetch({
      "/api/conversations": () => new Response(JSON.stringify(CONVERSATION), { status: 201 }),
      "/api/voice/transcriptions": () =>
        new Response(JSON.stringify({ text: "Yarın saat 10'da diş hekimi", audio_seconds: 1, language: "tr" })),
      "/api/conversations/c1/messages": () =>
        sseResponse([
          'data: {"type":"draft","kind":"task","source_message_id":"u1","title":"Diş hekimi","due_date":"2026-11-21"}\n\n',
          'data: {"type":"chunk","text":"Görev taslağını hazırladım, karta bakabilirsin."}\n\n',
          DONE,
        ]),
      "/api/voice/speech": () => wavResponse(),
      "/api/tasks": () =>
        new Response(JSON.stringify({ id: "t1", title: "Diş hekimi" }), { status: 201 }),
    });
    render(<VoiceView depsOverride={baseDeps()} />);
    await holdToTalk();
    expect(await screen.findByTestId("task-draft-card")).toHaveTextContent("Diş hekimi");
    await waitFor(() => expect(screen.getByRole("img", { name: "Friday boşta" })).toBeInTheDocument());
    // No task exists until the card is clicked.
    expect(calls.some((c) => c.url === "/api/tasks")).toBe(false);
    const spokenBefore = calls.filter((c) => c.url === "/api/voice/speech").length;
    await userEvent.click(screen.getByRole("button", { name: "Görevi onayla" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/tasks")).toHaveLength(1));
    // The spoken confirmation comes only after the receipt (task id) exists.
    await waitFor(() =>
      expect(calls.filter((c) => c.url === "/api/voice/speech").length).toBe(spokenBefore + 1),
    );
    const last = calls.filter((c) => c.url === "/api/voice/speech").at(-1)!;
    expect(JSON.parse(String(last.init!.body)).text).toMatch(/Görev oluşturuldu/);
  });

  it("'hatırla ki' creates a pending memory proposal, never an approved row", async () => {
    const calls = stubFetch({
      "/api/conversations": () => new Response(JSON.stringify(CONVERSATION), { status: 201 }),
      "/api/voice/transcriptions": () =>
        new Response(JSON.stringify({ text: "Hatırla ki kahvemi sade içerim.", audio_seconds: 1, language: "tr" })),
      "/api/memories": () =>
        new Response(
          JSON.stringify({ id: "m1", title: "Kahvemi sade içerim.", status: "pending_approval" }),
          { status: 201 },
        ),
      "/api/voice/speech": () => wavResponse(),
    });
    render(<VoiceView depsOverride={baseDeps()} />);
    await holdToTalk();
    expect(await screen.findByTestId("voice-proposals")).toHaveTextContent("Kahvemi sade içerim.");
    const create = calls.find((c) => c.url === "/api/memories")!;
    expect(JSON.parse(String(create.init!.body))).toMatchObject({
      workspace: "w1",
      content: "Kahvemi sade içerim.",
      proposal: true,
    });
    expect(calls.some((c) => c.url.includes("/messages"))).toBe(false);
  });

  it("explains a denied microphone permission in Turkish", async () => {
    stubFetch({});
    const startCapture = vi.fn(async () => {
      throw new CaptureError("mic_denied");
    });
    render(<VoiceView depsOverride={{ ...baseDeps(), startCapture }} />);
    await holdToTalk();
    expect(await screen.findByRole("alert")).toHaveTextContent("Mikrofon izni verilmedi");
  });

  it("explains an exhausted audio budget in Turkish", async () => {
    stubFetch({
      "/api/conversations": () => new Response(JSON.stringify(CONVERSATION), { status: 201 }),
      "/api/voice/transcriptions": () =>
        new Response(JSON.stringify({ code: "voice.budget_exceeded" }), { status: 429 }),
    });
    render(<VoiceView depsOverride={baseDeps()} />);
    await holdToTalk();
    expect(await screen.findByRole("alert")).toHaveTextContent("Ses bütçesi doldu");
  });

  it("explains a missing local speech provider in Turkish", async () => {
    stubFetch({
      "/api/conversations": () => new Response(JSON.stringify(CONVERSATION), { status: 201 }),
      "/api/voice/transcriptions": () =>
        new Response(JSON.stringify({ code: "voice.no_local_provider" }), { status: 503 }),
    });
    render(<VoiceView depsOverride={baseDeps()} />);
    await holdToTalk();
    expect(await screen.findByRole("alert")).toHaveTextContent("yerel konuşma sunucusunu başlat");
  });

  it("while muted the mic is disabled and nothing is captured", async () => {
    window.localStorage.setItem(MUTE_STORAGE_KEY, "1");
    stubFetch({});
    const capture = fakeCapture();
    render(<VoiceView depsOverride={baseDeps(capture)} />);
    expect(screen.getByTestId("voice-muted")).toBeInTheDocument();
    expect(screen.getByTestId("voice-mic")).toBeDisabled();
    expect(capture.startCapture).not.toHaveBeenCalled();
  });

  it("'Göndermeden önce göster' holds the transcript for editing before sending", async () => {
    const calls = stubFetch({
      "/api/conversations": () => new Response(JSON.stringify(CONVERSATION), { status: 201 }),
      "/api/voice/transcriptions": () =>
        new Response(JSON.stringify({ text: "Yarın toplantı var mı", audio_seconds: 1, language: "tr" })),
      "/api/conversations/c1/messages": () => sseResponse([DONE]),
    });
    render(<VoiceView depsOverride={baseDeps()} />);
    await userEvent.click(screen.getByRole("checkbox", { name: "Göndermeden önce göster" }));
    await holdToTalk();
    const editor = await screen.findByLabelText(/Duyduğum metin/);
    expect(editor).toHaveValue("Yarın toplantı var mı");
    expect(calls.some((c) => c.url.includes("/messages"))).toBe(false);
    await userEvent.type(editor, "?");
    await userEvent.click(within(screen.getByTestId("voice-review")).getByRole("button", { name: "Gönder" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/messages"))).toBe(true));
    const body = JSON.parse(String(calls.find((c) => c.url.includes("/messages"))!.init!.body));
    expect(body).toMatchObject({ content: "Yarın toplantı var mı?", input_mode: "voice" });
  });

  it("Escape stops listening and releases the mic", async () => {
    stubFetch({ "/api/conversations": () => new Response(JSON.stringify(CONVERSATION), { status: 201 }) });
    const capture = fakeCapture();
    render(<VoiceView depsOverride={baseDeps(capture)} />);
    await act(async () => {
      fireEvent.pointerDown(screen.getByTestId("voice-mic"), { button: 0 });
    });
    expect(screen.getByRole("img", { name: "Friday dinliyor" })).toBeInTheDocument();
    expect(document.title.startsWith("● Dinliyor")).toBe(true);
    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(capture.session.cancel).toHaveBeenCalled();
    expect(screen.getByRole("img", { name: "Friday boşta" })).toBeInTheDocument();
    expect(document.title.startsWith("● Dinliyor")).toBe(false);
  });

  // --- Phase 6 A8: the HUD variant and the published presence ---

  it("publishes its phase and captions as the tab's voice presence, cleared on unmount", async () => {
    stubFetch({
      "/api/conversations": () => new Response(JSON.stringify(CONVERSATION), { status: 201 }),
      "/api/voice/transcriptions": () =>
        new Response(JSON.stringify({ text: "Yarın hava nasıl?", audio_seconds: 1, language: "tr" })),
      "/api/conversations/c1/messages": () =>
        sseResponse(['data: {"type":"chunk","text":"Güneşli."}\n\n', DONE]),
      "/api/voice/speech": () => wavResponse(),
    });
    const { unmount } = render(<VoiceView depsOverride={baseDeps()} />);
    expect(readVoicePresence()).toMatchObject({ phase: "idle", active: true });
    await holdToTalk();
    await waitFor(() => expect(readVoicePresence().reply).toBe("Güneşli."));
    expect(readVoicePresence().heard).toBe("Yarın hava nasıl?");
    unmount();
    expect(readVoicePresence()).toMatchObject({ phase: "idle", active: false, heard: "" });
  });

  it("hud variant: controls only -- no orb, no captions, no typed composer or wake settings", async () => {
    stubFetch({
      "/api/conversations": () => new Response(JSON.stringify(CONVERSATION), { status: 201 }),
      "/api/voice/transcriptions": () =>
        new Response(JSON.stringify({ text: "Merhaba", audio_seconds: 1, language: "tr" })),
      "/api/conversations/c1/messages": () => sseResponse(['data: {"type":"chunk","text":"Selam."}\n\n', DONE]),
      "/api/voice/speech": () => wavResponse(),
    });
    render(<VoiceView variant="hud" depsOverride={baseDeps()} />);
    expect(screen.getByTestId("voice-mic")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByPlaceholderText("Friday'ye yaz…")).toBeNull();
    // The HUD announces state itself: the hint is not a second live region.
    expect(screen.getByTestId("voice-status")).not.toHaveAttribute("aria-live");
    await holdToTalk();
    await waitFor(() => expect(readVoicePresence().reply).toBe("Selam."));
    expect(screen.queryByTestId("voice-captions")).toBeNull();
  });
});
