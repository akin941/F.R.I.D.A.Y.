import { describe, expect, it, vi } from "vitest";
import type { CaptureCallbacks, CaptureSession } from "@/lib/voice/capture";
import { VoiceController, type VoiceControllerDeps, type VoiceSnapshot } from "@/lib/voice/controller";
import type { ApiUsageReceipt } from "@/lib/voice/receipts";
import type { ChatStreamEvent } from "@/types/api";

const flush = async () => {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

function fakeAudioContext() {
  const sources: { end(): void; start: ReturnType<typeof vi.fn> }[] = [];
  const context = {
    state: "running",
    destination: {},
    createAnalyser: () => ({ fftSize: 32, connect: vi.fn(), getFloatTimeDomainData: vi.fn() }),
    decodeAudioData: vi.fn(async () => ({}) as AudioBuffer),
    createBufferSource: () => {
      const node = {
        buffer: null as unknown,
        onended: null as (() => void) | null,
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn(),
        end: () => node.onended?.(),
      };
      sources.push(node);
      return node;
    },
    resume: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
  };
  return { context: context as unknown as AudioContext, sources };
}

interface Harness {
  controller: VoiceController;
  deps: VoiceControllerDeps;
  snapshots: VoiceSnapshot[];
  timers: { fn: () => void; ms: number }[];
  capture: { callbacks: CaptureCallbacks | null; session: CaptureSession & { cancel: ReturnType<typeof vi.fn> } };
  transcripts: string[];
  sentTurns: { content: string; inputMode: string; traceId: string | null }[];
  speechTexts: string[];
  audio: ReturnType<typeof fakeAudioContext>;
  muted: { muted: boolean; readable: boolean };
  replyFrames: ChatStreamEvent[];
  turnOutcome: "done" | "error" | "disconnected";
  elapse(): Promise<void>;
}

function harness(overrides: Partial<VoiceControllerDeps> = {}): Harness {
  const h = {
    snapshots: [],
    timers: [],
    transcripts: ["Merhaba Friday"],
    sentTurns: [],
    speechTexts: [],
    audio: fakeAudioContext(),
    muted: { muted: false, readable: true },
    replyFrames: [
      { type: "chunk", text: "Merhaba, bugün sana nasıl yardım edebilirim? " },
      { type: "chunk", text: "Takvimin boş görünüyor." },
      { type: "done", message_id: "a1", memories_used: [], chunks_used: [], overlay_source_workspaces: {} },
    ],
    turnOutcome: "done",
  } as unknown as Harness;
  const session = {
    samples: 16_000,
    stop: vi.fn(async () => new Uint8Array(new ArrayBuffer(46))),
    cancel: vi.fn(),
  } as unknown as Harness["capture"]["session"];
  h.capture = { callbacks: null, session };
  const deps: VoiceControllerDeps = {
    ensureConversation: vi.fn(async () => "c1"),
    workspaceId: () => "w1",
    startCapture: vi.fn(async (callbacks: CaptureCallbacks) => {
      h.capture.callbacks = callbacks;
      return session;
    }),
    transcribe: vi.fn(async () => ({ text: h.transcripts.shift() ?? "", audio_seconds: 1, language: "tr" })),
    sendTurn: vi.fn(async (_c, content, options, handlers) => {
      h.sentTurns.push({ content, inputMode: options.inputMode, traceId: options.traceId });
      for (const frame of h.replyFrames) {
        handlers.onEvent?.(frame);
        if (frame.type === "chunk") handlers.onChunk(frame.text);
      }
      return { outcome: h.turnOutcome };
    }),
    synthesize: vi.fn(async (_c, text) => {
      h.speechTexts.push(text);
      return new ArrayBuffer(8);
    }),
    fetchReceipt: vi.fn(
      async (): Promise<ApiUsageReceipt> => ({
        turn: null,
        today: { calls: 3, input_tokens: 0, output_tokens: 0, estimated_cost: "0" },
        currency: "USD",
      }),
    ),
    proposeMemory: vi.fn(async (_w, content) => ({ id: "m1", content }) as never),
    setMuted: vi.fn((value: boolean) => {
      h.muted = { muted: value, readable: true };
    }),
    readMute: () => h.muted,
    createAudioContext: () => h.audio.context,
    clientLocalDate: () => "2026-11-20",
    previewBeforeSend: () => false,
    finishMs: 900,
    onSnapshot: (s) => h.snapshots.push(s),
    setTimer: (fn, ms) => {
      h.timers.push({ fn, ms });
      return h.timers.length;
    },
    clearTimer: (handle) => {
      const timer = h.timers[(handle as number) - 1];
      if (timer) timer.fn = () => undefined;
    },
    ...overrides,
  };
  h.deps = deps;
  h.controller = new VoiceController(deps);
  h.elapse = async () => {
    const pending = h.timers.splice(0);
    for (const timer of pending) timer.fn();
    await flush();
  };
  return h;
}

async function speakTurn(h: Harness) {
  await h.controller.press();
  await h.controller.release();
}

describe("VoiceController", () => {
  it("push-to-talk: transcribe -> finish window -> voice turn -> sentences spoken", async () => {
    const h = harness();
    await speakTurn(h);
    expect(h.controller.snapshot.phase).toBe("finishing");
    // Nothing dispatched until the finish window elapses.
    expect(h.sentTurns).toEqual([]);
    expect(h.timers[0]!.ms).toBe(900);
    await h.elapse();
    expect(h.sentTurns).toHaveLength(1);
    expect(h.sentTurns[0]).toMatchObject({ content: "Merhaba Friday", inputMode: "voice" });
    // One trace id for the transcription, the message and every sentence.
    const trace = h.sentTurns[0]!.traceId!;
    expect(vi.mocked(h.deps.transcribe).mock.calls[0]![2]).toBe(trace);
    expect(vi.mocked(h.deps.synthesize).mock.calls.every((call) => call[2] === trace)).toBe(true);
    expect(h.speechTexts).toEqual([
      "Merhaba, bugün sana nasıl yardım edebilirim?",
      "Takvimin boş görünüyor.",
    ]);
    expect(h.controller.snapshot.reply).toContain("Takvimin boş");
    // Playback runs; the last sentence ending returns to idle.
    expect(h.controller.snapshot.phase).toBe("speaking");
    h.audio.sources[0]!.end();
    await flush();
    h.audio.sources[1]!.end();
    await flush();
    expect(h.controller.snapshot.phase).toBe("idle");
  });

  it("reports integer stage timings with the speech requests", async () => {
    let now = 0;
    const h = harness({ now: () => now });
    await h.controller.press();
    now = 1000;
    await h.controller.release();
    now = 1900;
    await h.elapse();
    const timings = vi.mocked(h.deps.synthesize).mock.calls.at(-1)![3];
    expect(timings).toMatchObject({ end_of_turn_ms: 900, llm_first_sentence_ms: 0, total_ms: 900 });
    for (const value of Object.values(timings)) expect(Number.isInteger(value)).toBe(true);
  });

  it("a pause shorter than VOICE_FINISH_MS appends instead of dispatching", async () => {
    const h = harness();
    h.transcripts = ["Yarın saat onda", "diş hekimi randevusu"];
    await speakTurn(h);
    expect(h.controller.snapshot.phase).toBe("finishing");
    // The owner presses again inside the window: the timer is cancelled.
    await speakTurn(h);
    await h.elapse();
    expect(h.sentTurns).toHaveLength(1);
    expect(h.sentTurns[0]!.content).toBe("Yarın saat onda diş hekimi randevusu");
    // Both pieces share the turn's trace id.
    const calls = vi.mocked(h.deps.transcribe).mock.calls;
    expect(calls[0]![2]).toBe(calls[1]![2]);
  });

  it("an interrupt word stops playback without any request", async () => {
    const h = harness();
    h.transcripts = ["Merhaba Friday", "dur"];
    await speakTurn(h);
    await h.elapse();
    expect(h.controller.snapshot.phase).toBe("speaking");
    const turns = h.sentTurns.length;
    const speech = vi.mocked(h.deps.synthesize).mock.calls.length;
    await speakTurn(h);
    expect(h.controller.snapshot.phase).toBe("idle");
    expect(h.timers).toHaveLength(0); // bypasses the finish window
    await h.elapse();
    expect(h.sentTurns).toHaveLength(turns);
    expect(vi.mocked(h.deps.synthesize).mock.calls).toHaveLength(speech);
  });

  it("an interrupt word discards a buffered utterance", async () => {
    const h = harness();
    h.transcripts = ["Annemi ara", "bekle"];
    await speakTurn(h);
    await speakTurn(h);
    await h.elapse();
    expect(h.sentTurns).toEqual([]);
  });

  it("'hatırla ki' files a memory PROPOSAL and confirms only with its receipt id", async () => {
    const h = harness();
    const onMemoryProposal = vi.fn();
    h.deps.onMemoryProposal = onMemoryProposal;
    h.transcripts = ["Hatırla ki kahvemi sade içerim."];
    await speakTurn(h);
    await h.elapse();
    expect(h.deps.proposeMemory).toHaveBeenCalledWith("w1", "Kahvemi sade içerim.");
    expect(onMemoryProposal).toHaveBeenCalledWith(expect.objectContaining({ id: "m1" }));
    expect(h.sentTurns).toEqual([]); // never reaches the brain
    expect(h.speechTexts.join(" ")).toMatch(/hafıza önerisi/);
  });

  it("a memory proposal without an id is never confirmed", async () => {
    const h = harness({ proposeMemory: vi.fn(async () => ({ id: "" }) as never) });
    h.transcripts = ["Hatırla ki kahvemi sade içerim."];
    await speakTurn(h);
    await h.elapse();
    expect(h.speechTexts).toEqual([]);
  });

  it("'bu ne kadar tuttu?' reads the receipt of the previous turn's trace", async () => {
    const h = harness();
    h.transcripts = ["Merhaba Friday", "Bu ne kadar tuttu?"];
    await speakTurn(h);
    await h.elapse();
    const firstTrace = h.sentTurns[0]!.traceId;
    h.controller.stop();
    await speakTurn(h);
    await h.elapse();
    expect(h.deps.fetchReceipt).toHaveBeenCalledWith("c1", firstTrace);
    expect(h.sentTurns).toHaveLength(1);
    expect(h.speechTexts.at(-1)).toMatch(/Bugün bu çalışma alanında toplam 3 çağrı/);
  });

  it("a spoken 'evet' confirms nothing: fixed reply, no turn, no receipt", async () => {
    const h = harness();
    h.transcripts = ["Evet, onayla."];
    await speakTurn(h);
    await h.elapse();
    expect(h.sentTurns).toEqual([]);
    expect(h.deps.proposeMemory).not.toHaveBeenCalled();
    expect(h.speechTexts).toEqual([expect.stringMatching(/Sesle onay veremem/)]);
    expect(h.controller.announce({ kind: "task", taskId: "" })).toBe(false);
  });

  it("announce speaks a confirmation only with a receipt id", () => {
    const h = harness();
    expect(h.controller.announce({ kind: "approval_request", approvalId: "" })).toBe(false);
    expect(h.controller.announce({ kind: "task", taskId: "t1" })).toBe(true);
  });

  it("'sessize al' sets the mute flag", async () => {
    const h = harness();
    h.transcripts = ["Sessize al"];
    await speakTurn(h);
    await h.elapse();
    expect(h.deps.setMuted).toHaveBeenCalledWith(true);
    expect(h.sentTurns).toEqual([]);
  });

  it("draft events reach the view as cards and nothing is confirmed", async () => {
    const h = harness();
    const onDraft = vi.fn();
    h.deps.onDraft = onDraft;
    h.replyFrames = [
      { type: "draft", kind: "task", source_message_id: "u1", title: "Diş hekimi", due_date: "2026-11-21" },
      { type: "chunk", text: "Görev taslağını hazırladım, kontrol eder misin?" },
      { type: "done", message_id: "a1", memories_used: [], chunks_used: [], overlay_source_workspaces: {} },
    ];
    await speakTurn(h);
    await h.elapse();
    expect(onDraft).toHaveBeenCalledWith(expect.objectContaining({ kind: "task", title: "Diş hekimi" }));
    expect(h.speechTexts[0]).toMatch(/karta tıkla/);
    expect(h.speechTexts.join(" ")).not.toMatch(/oluşturuldu/);
  });

  it("with mute set nothing is captured and nothing is played", async () => {
    const h = harness();
    h.muted = { muted: true, readable: true };
    await h.controller.press();
    expect(h.deps.startCapture).not.toHaveBeenCalled();
    expect(h.controller.snapshot.notice).toBe("muted");
    await h.controller.sendText("Merhaba Friday", "voice");
    expect(h.deps.synthesize).not.toHaveBeenCalled();
    expect(h.audio.sources).toHaveLength(0);
  });

  it("an unreadable mute state refuses the mic (fail-closed)", async () => {
    const h = harness();
    h.muted = { muted: true, readable: false };
    await h.controller.press();
    expect(h.deps.startCapture).not.toHaveBeenCalled();
    expect(h.controller.snapshot.notice).toBe("mute_unreadable");
  });

  it("muting while listening cancels the capture and releases the mic", async () => {
    const h = harness();
    await h.controller.press();
    h.controller.muteChanged({ muted: true, readable: true });
    expect(h.capture.session.cancel).toHaveBeenCalled();
    expect(h.controller.snapshot.phase).toBe("idle");
  });

  it("a dropped turn connection fails closed: no open mic, error state", async () => {
    const h = harness();
    h.turnOutcome = "disconnected";
    h.replyFrames = [{ type: "chunk", text: "Yarım" }];
    await speakTurn(h);
    await h.elapse();
    expect(h.controller.snapshot.phase).toBe("error");
    expect(h.controller.snapshot.error).toBe("disconnected");
  });

  it("stop mid-transcription aborts it and sends nothing", async () => {
    let resolveStt: (value: { text: string; audio_seconds: number; language: string }) => void = () => {};
    const h = harness({
      transcribe: vi.fn(
        (_c, _w, _t, signal: AbortSignal) =>
          new Promise<{ text: string; audio_seconds: number; language: string }>((resolve, reject) => {
            resolveStt = resolve;
            signal.addEventListener("abort", () => reject(new DOMException("x", "AbortError")));
          }),
      ),
    });
    await h.controller.press();
    const releasing = h.controller.release();
    await flush();
    expect(h.controller.snapshot.phase).toBe("transcribing");
    h.controller.stop();
    resolveStt({ text: "geç", audio_seconds: 1, language: "tr" });
    await releasing;
    await h.elapse();
    expect(h.controller.snapshot.phase).toBe("idle");
    expect(h.sentTurns).toEqual([]);
  });

  it("stop mid-playback aborts pending synthesis and goes idle", async () => {
    const h = harness();
    await speakTurn(h);
    await h.elapse();
    expect(h.controller.snapshot.phase).toBe("speaking");
    h.controller.stop();
    expect(h.controller.snapshot.phase).toBe("idle");
    const played = h.audio.sources.length;
    await flush();
    expect(h.audio.sources).toHaveLength(played);
  });

  it("budget_exceeded on transcription is an explained error state", async () => {
    const { ApiError } = await import("@/lib/api-client");
    const h = harness({
      transcribe: vi.fn(async () => {
        throw new ApiError(429, { code: "voice.budget_exceeded" });
      }),
    });
    await speakTurn(h);
    expect(h.controller.snapshot).toMatchObject({ phase: "error", error: "voice.budget_exceeded" });
  });

  it("mic permission denied is an explained error state", async () => {
    const { CaptureError } = await import("@/lib/voice/capture");
    const h = harness({
      startCapture: vi.fn(async () => {
        throw new CaptureError("mic_denied");
      }),
    });
    await h.controller.press();
    expect(h.controller.snapshot).toMatchObject({ phase: "error", error: "mic_denied" });
  });

  it("'Göndermeden önce göster' holds the transcript for editing", async () => {
    const h = harness({ previewBeforeSend: () => true });
    await speakTurn(h);
    await h.elapse();
    expect(h.sentTurns).toEqual([]);
    expect(h.controller.snapshot.review).toBe("Merhaba Friday");
    await h.controller.sendText("Merhaba Friday, nasılsın?", "voice");
    expect(h.sentTurns[0]).toMatchObject({ content: "Merhaba Friday, nasılsın?", inputMode: "voice" });
  });

  it("a typed turn is sent as text and not spoken", async () => {
    const h = harness();
    await h.controller.sendText("Merhaba", "text");
    expect(h.sentTurns[0]).toMatchObject({ inputMode: "text", traceId: null });
    expect(h.deps.synthesize).not.toHaveBeenCalled();
  });

  it("empty audio uploads nothing", async () => {
    const h = harness();
    vi.mocked(h.capture.session.stop).mockResolvedValueOnce(null);
    await speakTurn(h);
    expect(h.deps.transcribe).not.toHaveBeenCalled();
    expect(h.controller.snapshot.phase).toBe("idle");
  });
});
