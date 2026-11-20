import { describe, expect, it, vi } from "vitest";
import {
  applyHudFrame,
  HudStream,
  hudEventsUrl,
  parseHudFrame,
  type HudData,
  type HudStreamStatus,
} from "@/lib/hud/events";

export const SAMPLE: HudData = {
  approvals: { pending_count: 0, items: [], tools_running: 0 },
  tasks: { date: "2026-11-20", open_count: 0, items: [] },
  cost: {
    since: null,
    calls: 0,
    failed_calls: 0,
    input_tokens: 0,
    output_tokens: 0,
    estimated_cost: "0.000000",
    local_only: true,
  },
  health: { providers: [], providers_down: 0, budgets: [], budgets_exhausted: 0, preflight: null },
};

const frame = (payload: unknown) => `data: ${JSON.stringify(payload)}\n\n`;
const snapshot = (data: HudData = SAMPLE) =>
  frame({ type: "snapshot", workspace: "w1", interval_seconds: 5, ...data });

function streamResponse(chunks: string[], { close = true } = {}) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        if (close) controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

function harness(responses: (() => Response | Promise<Response>)[]) {
  const timers: { cb: () => void; ms: number }[] = [];
  const statuses: HudStreamStatus[] = [];
  const data: HudData[] = [];
  const fetch = vi.fn(async (...args: [string, RequestInit]) => {
    void args;
    const next = responses.shift();
    if (!next) return new Promise<Response>(() => undefined);
    return next();
  });
  const stream = new HudStream(
    "/api/hud/events?workspace=w1",
    { onData: (d) => data.push(d), onStatus: (s) => statuses.push(s) },
    {
      fetch,
      setTimeout: (cb, ms) => {
        timers.push({ cb, ms });
        return timers.length;
      },
      clearTimeout: vi.fn(),
    },
  );
  const flush = async () => {
    for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0));
  };
  return { stream, fetch, timers, statuses, data, flush };
}

describe("parseHudFrame", () => {
  it("parses snapshot, delta, end and retry frames", () => {
    expect(parseHudFrame(snapshot().trim())).toMatchObject({ type: "snapshot", workspace: "w1" });
    expect(
      parseHudFrame(frame({ type: "delta", cost: SAMPLE.cost, bogus: { x: 1 } }).trim()),
    ).toEqual({ type: "delta", cost: SAMPLE.cost });
    expect(parseHudFrame(frame({ type: "end", reason: "max_age" }).trim())).toEqual({
      type: "end",
      reason: "max_age",
    });
    expect(parseHudFrame("retry: 3000")).toEqual({ type: "retry", ms: 3000 });
  });

  it("ignores comments, junk and incomplete snapshots", () => {
    expect(parseHudFrame(": keepalive")).toBeNull();
    expect(parseHudFrame("data: {not json")).toBeNull();
    expect(parseHudFrame(frame({ type: "snapshot", approvals: {} }).trim())).toBeNull();
    expect(parseHudFrame(frame({ type: "other" }).trim())).toBeNull();
  });
});

describe("applyHudFrame", () => {
  it("replaces on snapshot and merges changed sections on delta", () => {
    const first = applyHudFrame(null, { type: "snapshot", workspace: "w1", interval_seconds: 5, ...SAMPLE });
    const approvals = { pending_count: 1, items: [], tools_running: 0 };
    const next = applyHudFrame(first, { type: "delta", approvals });
    expect(next?.approvals).toEqual(approvals);
    expect(next?.cost).toBe(SAMPLE.cost);
    // A delta before any snapshot has nothing to apply to.
    expect(applyHudFrame(null, { type: "delta", approvals })).toBeNull();
  });
});

describe("HudStream", () => {
  it("builds the BFF url with the client date", () => {
    expect(hudEventsUrl("w1", "2026-11-20")).toBe("/api/hud/events?workspace=w1&date=2026-11-20");
  });

  it("opens on the snapshot, applies deltas and reconnects quietly after an end frame", async () => {
    const h = harness([
      () =>
        streamResponse([
          "retry: 2500\n\n",
          snapshot(),
          ": keepalive\n\n",
          frame({ type: "delta", approvals: { pending_count: 2, items: [], tools_running: 0 } }),
          frame({ type: "end", reason: "max_age" }),
        ]),
    ]);
    h.stream.start();
    await h.flush();
    expect(h.statuses).toEqual(["connecting", "open"]);
    expect(h.data).toHaveLength(2);
    expect(h.data[1]!.approvals.pending_count).toBe(2);
    // Planned close: a reconnect after the retry hint, never "lost".
    expect(h.timers).toEqual([{ cb: expect.any(Function), ms: 2500 }]);
    expect(h.fetch.mock.calls[0]![1].headers).toEqual({ Accept: "text/event-stream" });
  });

  it("is lost when the stream closes without an end frame, then retries", async () => {
    const h = harness([() => streamResponse([snapshot()]), () => streamResponse([snapshot()])]);
    h.stream.start();
    await h.flush();
    expect(h.statuses).toEqual(["connecting", "open", "lost"]);
    expect(h.timers[0]!.ms).toBe(3000);
    h.timers[0]!.cb();
    await h.flush();
    // Reopened on the new snapshot (and lost again: that stream also closed).
    expect(h.statuses).toEqual(["connecting", "open", "lost", "open", "lost"]);
  });

  it("backs off on refusals and network errors", async () => {
    const h = harness([
      () => new Response(JSON.stringify({ code: "owner_required" }), { status: 403 }),
      () => Promise.reject(new TypeError("network")),
    ]);
    h.stream.start();
    await h.flush();
    expect(h.statuses).toEqual(["connecting", "lost"]);
    h.timers[0]!.cb();
    await h.flush();
    expect(h.timers.map((t) => t.ms)).toEqual([3000, 6000]);
  });

  it("stop() aborts the request and schedules nothing", async () => {
    const h = harness([() => streamResponse([snapshot()], { close: false })]);
    h.stream.start();
    await h.flush();
    const signal = h.fetch.mock.calls[0]![1].signal as AbortSignal;
    h.stream.stop();
    await h.flush();
    expect(signal.aborted).toBe(true);
    expect(h.timers).toEqual([]);
    expect(h.statuses).toEqual(["connecting", "open"]);
  });
});
