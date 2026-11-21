/**
 * The HUD terminal panel's stream client (Phase 10 D12): `GET
 * /api/hud/terminal/<execution_id>/stream` (BFF) -> backend `GET
 * /api/v1/hud/terminal/<execution_id>/stream`. Server-Sent Events over
 * `fetch` (the HUD events pattern). Frames:
 *
 * - `start` (the execution's summary), `chunk` `{seq, stream, text}` (each
 *   with an SSE `id:`), then a final `end` `{status, exit_code, truncated}`;
 * - `end` with a `reason` (`max_age`, `scope_lost`): the server closed a
 *   bounded window; the client reconnects with `Last-Event-ID` and resumes
 *   after the last chunk it saw.
 *
 * The output is untrusted, already scrubbed and sanitised server-side text.
 * This module only parses it; the panel renders it as text nodes.
 */

import { apiFetch } from "@/lib/api-client";

export interface TerminalChunk {
  seq: number;
  stream: "stdout" | "stderr" | string;
  text: string;
}

export interface TerminalFinal {
  status: string;
  exit_code: number | null;
  truncated: boolean;
}

export type TerminalFrame =
  | { type: "start"; status: string; label: string; repo_name: string }
  | ({ type: "chunk"; id: string | null } & TerminalChunk)
  | ({ type: "final" } & TerminalFinal)
  | { type: "window_end"; reason: string }
  | { type: "retry"; ms: number };

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function terminalStreamUrl(executionId: string): string {
  return `/api/hud/terminal/${encodeURIComponent(executionId)}/stream`;
}

/** One raw SSE frame -> a typed frame, or null. */
export function parseTerminalFrame(raw: string): TerminalFrame | null {
  const lines = raw.split(/\r\n|\n/);
  const retry = lines.find((line) => line.startsWith("retry:"));
  if (retry) {
    const ms = Number(retry.slice(6).trim());
    return Number.isFinite(ms) && ms > 0 ? { type: "retry", ms } : null;
  }
  const idLine = lines.find((line) => line.startsWith("id:"));
  const id = idLine ? idLine.slice(3).trim() : null;
  const data = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5).replace(/^ /, ""));
  if (data.length === 0) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(data.join("\n"));
  } catch {
    return null;
  }
  if (!isObject(payload) || typeof payload.type !== "string") return null;
  if (payload.type === "start") {
    return {
      type: "start",
      status: typeof payload.status === "string" ? payload.status : "",
      label: typeof payload.label === "string" ? payload.label : "",
      repo_name: typeof payload.repo_name === "string" ? payload.repo_name : "",
    };
  }
  if (payload.type === "chunk") {
    if (typeof payload.text !== "string" || typeof payload.seq !== "number") return null;
    return {
      type: "chunk",
      id,
      seq: payload.seq,
      stream: typeof payload.stream === "string" ? payload.stream : "stdout",
      text: payload.text,
    };
  }
  if (payload.type === "end") {
    if (typeof payload.status === "string") {
      return {
        type: "final",
        status: payload.status,
        exit_code: typeof payload.exit_code === "number" ? payload.exit_code : null,
        truncated: payload.truncated === true,
      };
    }
    return { type: "window_end", reason: typeof payload.reason === "string" ? payload.reason : "" };
  }
  return null;
}

export type TerminalStreamStatus = "connecting" | "open" | "done" | "lost";

export interface TerminalStreamCallbacks {
  onChunk: (chunk: TerminalChunk) => void;
  onFinal: (final: TerminalFinal) => void;
  onStatus: (status: TerminalStreamStatus) => void;
}

export interface TerminalStreamDeps {
  fetch: (url: string, init: RequestInit) => Promise<Response>;
  setTimeout: (cb: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
}

const DEFAULT_RETRY_MS = 3000;
const MAX_FAILURES = 5;

export class TerminalStream {
  private readonly url: string;
  private readonly callbacks: TerminalStreamCallbacks;
  private readonly deps: TerminalStreamDeps;
  private controller: AbortController | null = null;
  private timer: unknown = null;
  private stopped = true;
  private retryMs = DEFAULT_RETRY_MS;
  private failures = 0;
  private lastId: string | null = null;
  private lastSeq = -1;

  constructor(url: string, callbacks: TerminalStreamCallbacks, deps?: Partial<TerminalStreamDeps>) {
    this.url = url;
    this.callbacks = callbacks;
    this.deps = {
      fetch: (input, init) => apiFetch(input, init),
      setTimeout: (cb, ms) => globalThis.setTimeout(cb, ms),
      clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
      ...deps,
    };
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.callbacks.onStatus("connecting");
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.controller?.abort();
    this.controller = null;
    if (this.timer !== null) this.deps.clearTimeout(this.timer);
    this.timer = null;
  }

  private schedule(ms: number) {
    if (this.stopped) return;
    this.timer = this.deps.setTimeout(() => {
      this.timer = null;
      void this.connect();
    }, ms);
  }

  private lost() {
    if (this.stopped) return;
    this.failures += 1;
    this.callbacks.onStatus("lost");
    if (this.failures < MAX_FAILURES) this.schedule(this.retryMs * 2 ** (this.failures - 1));
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;
    const controller = new AbortController();
    this.controller = controller;
    let windowEnded = false;
    let finished = false;
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (this.lastId) headers["Last-Event-ID"] = this.lastId;
    try {
      const response = await this.deps.fetch(this.url, { headers, signal: controller.signal, cache: "no-store" });
      if (!response.ok || !response.body) {
        this.lost();
        return;
      }
      this.failures = 0;
      this.callbacks.onStatus("open");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.search(/\r\n\r\n|\n\n/);
        while (boundary >= 0) {
          const raw = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary).replace(/^(\r\n\r\n|\n\n)/, "");
          const frame = parseTerminalFrame(raw);
          if (frame?.type === "retry") this.retryMs = frame.ms;
          else if (frame?.type === "chunk") {
            if (frame.id) this.lastId = frame.id;
            // A resumed stream never repeats a chunk already shown.
            if (frame.seq > this.lastSeq) {
              this.lastSeq = frame.seq;
              this.callbacks.onChunk({ seq: frame.seq, stream: frame.stream, text: frame.text });
            }
          } else if (frame?.type === "final") {
            finished = true;
            this.callbacks.onFinal({ status: frame.status, exit_code: frame.exit_code, truncated: frame.truncated });
          } else if (frame?.type === "window_end") windowEnded = true;
          boundary = buffer.search(/\r\n\r\n|\n\n/);
        }
      }
    } catch {
      if (this.stopped || controller.signal.aborted) return;
      this.lost();
      return;
    }
    if (this.stopped) return;
    if (finished) {
      this.stopped = true;
      this.callbacks.onStatus("done");
    } else if (windowEnded) this.schedule(this.retryMs);
    else this.lost();
  }
}
