/**
 * The HUD event stream client (Phase 6 brief A8): `GET /api/hud/events`
 * (BFF) -> backend `GET /api/v1/hud/events`. Server-Sent Events read over
 * `fetch` (not `EventSource`) so a 401 goes through the api-client's single
 * refresh-and-retry path like every other request.
 *
 * The backend closes each stream on purpose after a bounded window (an
 * `end` frame); the client then reconnects after the `retry:` hint without
 * flagging anything. Any other close, a non-2xx answer or a network error is
 * a LOST stream: the HUD shows *disconnected* and the client retries with a
 * capped backoff. The stream only reads: nothing here writes or decides.
 */

import { apiFetch } from "@/lib/api-client";

export interface HudApprovalItem {
  id: string;
  tool_name: string;
  origin: string;
  requested_channel: string;
  created_at: string;
}

export interface HudApprovals {
  pending_count: number;
  items: HudApprovalItem[];
  tools_running: number;
}

export interface HudTaskItem {
  id: string;
  title: string;
  due_date: string;
  priority: string;
  overdue: boolean;
}

export interface HudTasks {
  date: string;
  open_count: number;
  items: HudTaskItem[];
}

export interface HudCost {
  since: string | null;
  calls: number;
  failed_calls: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: string;
  local_only: boolean;
}

export type HudProviderStatus = "ok" | "down" | "unknown" | "disabled";

export interface HudProvider {
  name: string;
  capability: string;
  status: HudProviderStatus;
  personal_data_allowed: boolean;
  local: boolean;
  last_call: { at: string | null; error_type: string | null } | null;
}

export interface HudBudgetWindow {
  window: string;
  limit_usd: string;
  spent_usd: string;
  exhausted: boolean;
}

export interface HudBudget {
  name: string;
  windows: HudBudgetWindow[];
  exhausted: boolean;
}

export interface HudPreflight {
  status: "pass" | "fail" | "warn" | null;
  pass: number | null;
  fail: number | null;
  warn: number | null;
  at: string | null;
  failed_checks: string[] | null;
}

export interface HudHealth {
  providers: HudProvider[];
  providers_down: number;
  budgets: HudBudget[];
  budgets_exhausted: number;
  preflight: HudPreflight | null;
}

// Phase 10 D12: the actor's last five node action executions (no output).
export interface HudTerminalItem {
  id: string;
  capability: string;
  label: string;
  class: string;
  repo_name: string;
  status: string;
  running: boolean;
  terminal: boolean;
  exit_code: number | null;
  duration_ms: number;
  created_at: string | null;
  finished_at: string | null;
}

export interface HudTerminal {
  items: HudTerminalItem[];
  running_count: number;
}

export const EMPTY_TERMINAL: HudTerminal = { items: [], running_count: 0 };

export interface HudData {
  approvals: HudApprovals;
  tasks: HudTasks;
  cost: HudCost;
  health: HudHealth;
  /** Optional: absent from a backend predating Phase 10. */
  terminal?: HudTerminal;
}

export const HUD_SECTIONS = ["approvals", "tasks", "cost", "health"] as const;

export type HudFrame =
  | ({ type: "snapshot"; workspace: string; interval_seconds: number } & HudData)
  | ({ type: "delta" } & Partial<HudData>)
  | { type: "end"; reason: string }
  | { type: "retry"; ms: number };

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** One raw SSE frame -> a typed frame, or null (comment, unknown type). */
export function parseHudFrame(raw: string): HudFrame | null {
  const lines = raw.split(/\r\n|\n/);
  const retry = lines.find((line) => line.startsWith("retry:"));
  if (retry) {
    const ms = Number(retry.slice(6).trim());
    return Number.isFinite(ms) && ms > 0 ? { type: "retry", ms } : null;
  }
  const data = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""));
  if (data.length === 0) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(data.join("\n"));
  } catch {
    return null;
  }
  if (!isObject(payload) || typeof payload.type !== "string") return null;
  if (payload.type === "snapshot") {
    if (!HUD_SECTIONS.every((name) => isObject(payload[name]))) return null;
    return payload as HudFrame;
  }
  if (payload.type === "delta") {
    const delta: Record<string, unknown> = { type: "delta" };
    for (const name of HUD_SECTIONS) {
      if (isObject(payload[name])) delta[name] = payload[name];
    }
    if (isObject(payload.terminal)) delta.terminal = payload.terminal;
    return delta as HudFrame;
  }
  if (payload.type === "end") {
    return { type: "end", reason: typeof payload.reason === "string" ? payload.reason : "" };
  }
  return null;
}

/** Applies a snapshot (replace) or a delta (replace changed sections). */
export function applyHudFrame(data: HudData | null, frame: HudFrame): HudData | null {
  if (frame.type === "snapshot") {
    return {
      approvals: frame.approvals,
      tasks: frame.tasks,
      cost: frame.cost,
      health: frame.health,
      terminal: isObject(frame.terminal) ? frame.terminal : EMPTY_TERMINAL,
    };
  }
  if (frame.type === "delta") {
    if (!data) return data;
    return {
      approvals: frame.approvals ?? data.approvals,
      tasks: frame.tasks ?? data.tasks,
      cost: frame.cost ?? data.cost,
      health: frame.health ?? data.health,
      terminal: frame.terminal ?? data.terminal,
    };
  }
  return data;
}

export type HudStreamStatus = "connecting" | "open" | "lost";

export interface HudStreamCallbacks {
  onData: (data: HudData) => void;
  onStatus: (status: HudStreamStatus) => void;
}

export interface HudStreamDeps {
  fetch: (url: string, init: RequestInit) => Promise<Response>;
  setTimeout: (cb: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
}

const DEFAULT_RETRY_MS = 3000;
const MAX_BACKOFF_MS = 60_000;

export function hudEventsUrl(workspace: string, date?: string): string {
  const query = new URLSearchParams({ workspace });
  if (date) query.set("date", date);
  return `/api/hud/events?${query.toString()}`;
}

export class HudStream {
  private readonly url: string;
  private readonly callbacks: HudStreamCallbacks;
  private readonly deps: HudStreamDeps;
  private controller: AbortController | null = null;
  private timer: unknown = null;
  private stopped = true;
  private retryMs = DEFAULT_RETRY_MS;
  private failures = 0;
  private data: HudData | null = null;
  private status: HudStreamStatus | null = null;

  constructor(url: string, callbacks: HudStreamCallbacks, deps?: Partial<HudStreamDeps>) {
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
    this.setStatus("connecting");
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.controller?.abort();
    this.controller = null;
    if (this.timer !== null) this.deps.clearTimeout(this.timer);
    this.timer = null;
  }

  private setStatus(status: HudStreamStatus) {
    if (this.status === status) return;
    this.status = status;
    this.callbacks.onStatus(status);
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
    this.setStatus("lost");
    this.schedule(Math.min(MAX_BACKOFF_MS, this.retryMs * 2 ** (this.failures - 1)));
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;
    const controller = new AbortController();
    this.controller = controller;
    let ended = false;
    try {
      const response = await this.deps.fetch(this.url, {
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
        cache: "no-store",
      });
      if (!response.ok || !response.body) {
        this.lost();
        return;
      }
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
          const frame = parseHudFrame(raw);
          if (frame?.type === "retry") this.retryMs = frame.ms;
          else if (frame?.type === "end") ended = true;
          else if (frame) {
            const next = applyHudFrame(this.data, frame);
            if (next && next !== this.data) {
              this.data = next;
              if (frame.type === "snapshot") {
                this.failures = 0;
                this.setStatus("open");
              }
              this.callbacks.onData(next);
            }
          }
          boundary = buffer.search(/\r\n\r\n|\n\n/);
        }
      }
    } catch {
      if (this.stopped || controller.signal.aborted) return;
      this.lost();
      return;
    }
    if (this.stopped) return;
    if (ended) this.schedule(this.retryMs);
    else this.lost();
  }
}
