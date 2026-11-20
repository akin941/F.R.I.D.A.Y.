/**
 * The per-turn trace (Phase 6 A5): one client-generated UUID per spoken
 * turn, sent as `X-Friday-Trace` on the transcription, message and speech
 * requests, plus the client's own stage timings (integers, ms) reported
 * with the turn's speech requests (the server keeps the latest report).
 * Timings only -- never text or audio.
 */

export interface StageTimings {
  /** Finish window: transcript ready -> turn dispatched. */
  end_of_turn_ms?: number;
  /** Turn dispatched -> first speakable sentence of the reply. */
  llm_first_sentence_ms?: number;
  /** End of owner speech (mic released) -> first audio playing. */
  playback_start_ms?: number;
  /** End of owner speech -> the last sentence requested. */
  total_ms?: number;
}

export function newTraceId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // RFC 4122 v4 from getRandomValues (older Safari).
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

const MAX_STAGE_MS = 600_000;

type Mark = "released" | "transcribed" | "dispatched" | "firstSentence" | "playbackStart" | "lastRequest";

/** Collects one turn's marks and derives the reported stage timings. */
export class TurnTimer {
  private marks: Partial<Record<Mark, number>> = {};
  constructor(private readonly now: () => number = () => performance.now()) {}

  mark(name: Mark, overwrite = false): void {
    if (overwrite || this.marks[name] === undefined) this.marks[name] = this.now();
  }

  private span(from: Mark, to: Mark): number | undefined {
    const a = this.marks[from];
    const b = this.marks[to];
    if (a === undefined || b === undefined || b < a) return undefined;
    const ms = Math.round(b - a);
    return ms <= MAX_STAGE_MS ? ms : undefined;
  }

  timings(): StageTimings {
    const out: StageTimings = {};
    const set = (key: keyof StageTimings, value: number | undefined) => {
      if (value !== undefined) out[key] = value;
    };
    set("end_of_turn_ms", this.span("transcribed", "dispatched"));
    set("llm_first_sentence_ms", this.span("dispatched", "firstSentence"));
    set("playback_start_ms", this.span("released", "playbackStart"));
    set("total_ms", this.span("released", "lastRequest"));
    return out;
  }
}
