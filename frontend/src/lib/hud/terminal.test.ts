import { describe, expect, it, vi } from "vitest";
import { TerminalStream, parseTerminalFrame, terminalStreamUrl } from "@/lib/hud/terminal";

function sse(frames: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(frames.join("")));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const data = (payload: unknown, id?: string) => `${id ? `id: ${id}\n` : ""}data: ${JSON.stringify(payload)}\n\n`;

function deps(responses: Response[]) {
  const timers: (() => void)[] = [];
  const fetch = vi.fn(async () => responses.shift() ?? new Response(null, { status: 502 }));
  return {
    fetch,
    timers,
    deps: { fetch, setTimeout: (cb: () => void) => timers.push(cb), clearTimeout: () => undefined },
  };
}

describe("terminal stream client (Phase 10 D12)", () => {
  it("builds the BFF url", () => {
    expect(terminalStreamUrl("abc")).toBe("/api/hud/terminal/abc/stream");
  });

  it("parses start, chunk (with id), final and window end frames", () => {
    expect(parseTerminalFrame("retry: 3000")).toEqual({ type: "retry", ms: 3000 });
    expect(parseTerminalFrame(data({ type: "chunk", seq: 2, stream: "stderr", text: "x" }, "1-0").trim())).toEqual({
      type: "chunk",
      id: "1-0",
      seq: 2,
      stream: "stderr",
      text: "x",
    });
    expect(parseTerminalFrame(data({ type: "end", status: "ok", exit_code: 0, truncated: false }).trim())).toEqual({
      type: "final",
      status: "ok",
      exit_code: 0,
      truncated: false,
    });
    expect(parseTerminalFrame(data({ type: "end", reason: "max_age" }).trim())).toEqual({
      type: "window_end",
      reason: "max_age",
    });
    expect(parseTerminalFrame("data: not json")).toBeNull();
    expect(parseTerminalFrame(data({ type: "chunk", seq: "1", text: 5 }).trim())).toBeNull();
  });

  it("delivers chunks then the final status, and stops", async () => {
    const d = deps([
      sse([
        "retry: 100\n\n",
        data({ type: "start", status: "running", label: "L", repo_name: "acme" }),
        data({ type: "chunk", seq: 0, stream: "stdout", text: "a\n" }, "1-0"),
        data({ type: "end", status: "ok", exit_code: 0, truncated: false }, "2-0"),
      ]),
    ]);
    const onChunk = vi.fn();
    const onFinal = vi.fn();
    const statuses: string[] = [];
    const stream = new TerminalStream("/u", { onChunk, onFinal, onStatus: (s) => statuses.push(s) }, d.deps);
    stream.start();
    await vi.waitFor(() => expect(onFinal).toHaveBeenCalled());
    expect(onChunk).toHaveBeenCalledWith({ seq: 0, stream: "stdout", text: "a\n" });
    expect(onFinal).toHaveBeenCalledWith({ status: "ok", exit_code: 0, truncated: false });
    await vi.waitFor(() => expect(statuses).toContain("done"));
    expect(d.timers).toHaveLength(0);
  });

  it("resumes after a bounded window with Last-Event-ID and never repeats a chunk", async () => {
    const d = deps([
      sse([data({ type: "chunk", seq: 0, stream: "stdout", text: "a" }, "5-0"), data({ type: "end", reason: "max_age" })]),
      sse([
        data({ type: "chunk", seq: 0, stream: "stdout", text: "a" }, "5-0"),
        data({ type: "chunk", seq: 1, stream: "stdout", text: "b" }, "6-0"),
        data({ type: "end", status: "ok", exit_code: 0, truncated: false }),
      ]),
    ]);
    const onChunk = vi.fn();
    const onFinal = vi.fn();
    const stream = new TerminalStream("/u", { onChunk, onFinal, onStatus: () => undefined }, d.deps);
    stream.start();
    await vi.waitFor(() => expect(d.timers).toHaveLength(1));
    d.timers.shift()!();
    await vi.waitFor(() => expect(onFinal).toHaveBeenCalled());
    const secondInit = d.fetch.mock.calls[1] as unknown as [string, RequestInit];
    expect((secondInit[1].headers as Record<string, string>)["Last-Event-ID"]).toBe("5-0");
    expect(onChunk.mock.calls.map((c) => c[0].text)).toEqual(["a", "b"]);
  });

  it("marks a refused stream lost and backs off", async () => {
    const d = deps([new Response(JSON.stringify({}), { status: 404 })]);
    const statuses: string[] = [];
    const stream = new TerminalStream(
      "/u",
      { onChunk: () => undefined, onFinal: () => undefined, onStatus: (s) => statuses.push(s) },
      d.deps,
    );
    stream.start();
    await vi.waitFor(() => expect(statuses).toContain("lost"));
    expect(d.timers).toHaveLength(1);
    stream.stop();
  });
});
