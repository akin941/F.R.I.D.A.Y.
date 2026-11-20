"use client";

import { ApiError, apiFetch, apiPostJson } from "@/lib/api-client";
import { SSEStreamParser } from "@/lib/sse";
import { VOICE_LANGUAGE } from "@/lib/voice/config";
import type { ApiUsageReceipt } from "@/lib/voice/receipts";
import type { StageTimings } from "@/lib/voice/trace";
import type { ApiMemory, ChatStreamEvent } from "@/types/api";

/**
 * The voice loop's network edges (Phase 6 D1): same-origin BFF routes only.
 * Every request of a spoken turn carries the turn's trace id (A5).
 */
export const TRACE_HEADER = "X-Friday-Trace";

export interface Transcript {
  text: string;
  audio_seconds: number;
  language: string;
}

async function failure(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => ({}));
  return new ApiError(response.status, body);
}

export async function transcribe(
  conversationId: string,
  wav: Uint8Array<ArrayBuffer>,
  traceId: string,
  signal?: AbortSignal,
): Promise<Transcript> {
  const form = new FormData();
  form.append("conversation", conversationId);
  form.append("audio", new Blob([wav], { type: "audio/wav" }), "utterance.wav");
  const response = await apiFetch("/api/voice/transcriptions", {
    method: "POST",
    body: form,
    headers: { [TRACE_HEADER]: traceId },
    signal,
  });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as Transcript;
}

export async function synthesize(
  conversationId: string,
  text: string,
  traceId: string,
  timings: StageTimings,
  signal: AbortSignal,
): Promise<ArrayBuffer> {
  const response = await apiFetch("/api/voice/speech", {
    method: "POST",
    headers: { "Content-Type": "application/json", [TRACE_HEADER]: traceId },
    body: JSON.stringify({ conversation: conversationId, text, language: VOICE_LANGUAGE, timings }),
    signal,
  });
  if (!response.ok) throw await failure(response);
  if (!(response.headers.get("content-type") ?? "").startsWith("audio/wav")) {
    throw new ApiError(502, { code: "voice.tts_failed" });
  }
  return response.arrayBuffer();
}

export async function fetchReceipt(
  conversationId: string,
  traceId: string | null,
): Promise<ApiUsageReceipt> {
  const query = new URLSearchParams({ conversation: conversationId });
  if (traceId) query.set("trace", traceId);
  const response = await apiFetch(`/api/voice/receipt?${query.toString()}`, { method: "GET" });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as ApiUsageReceipt;
}

/**
 * "Hatırla ki …" (A6): a memory PROPOSAL through the existing memory API --
 * `proposal: true` births it `pending_approval`; the owner approves it by
 * click on the memory card. Never an approved row.
 */
export async function proposeMemory(workspaceId: string, content: string): Promise<ApiMemory> {
  const title = content.length > 60 ? `${content.slice(0, 57)}…` : content;
  return apiPostJson<ApiMemory>("/api/memories", {
    workspace: workspaceId,
    title,
    content,
    memory_type: "semantic",
    sensitivity: "normal",
    proposal: true,
  });
}

export type TurnOutcome = "done" | "error" | "aborted" | "disconnected" | "rejected";

export interface TurnHandlers {
  onChunk(text: string): void;
  onEvent?(event: ChatStreamEvent): void;
}

/**
 * One chat turn over the unchanged messages endpoint, with
 * `input_mode="voice"` for a spoken turn (D9; the server's ADR-015 cap then
 * limits it to reads and proposals).
 */
export async function sendTurn(
  conversationId: string,
  content: string,
  options: { inputMode: "voice" | "text"; traceId: string | null; clientLocalDate: string },
  handlers: TurnHandlers,
  signal: AbortSignal,
): Promise<{ outcome: TurnOutcome; error?: ApiError | string }> {
  let response: Response;
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (options.traceId) headers[TRACE_HEADER] = options.traceId;
    response = await apiFetch(`/api/conversations/${conversationId}/messages`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        content,
        client_local_date: options.clientLocalDate,
        input_mode: options.inputMode,
      }),
      signal,
    });
  } catch (err) {
    return { outcome: err instanceof DOMException && err.name === "AbortError" ? "aborted" : "disconnected" };
  }
  if (!response.ok) return { outcome: "rejected", error: await failure(response) };
  if (!response.body) return { outcome: "disconnected" };
  const reader = response.body.getReader();
  const parser = new SSEStreamParser();
  let outcome: TurnOutcome = "disconnected";
  let error: string | undefined;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const event of parser.push(value)) {
        handlers.onEvent?.(event);
        if (event.type === "chunk") handlers.onChunk(event.text);
        else if (event.type === "done") outcome = "done";
        else if (event.type === "error") {
          outcome = "error";
          error = event.error_type;
        }
      }
    }
  } catch (err) {
    return { outcome: err instanceof DOMException && err.name === "AbortError" ? "aborted" : "disconnected" };
  }
  return { outcome, error };
}
