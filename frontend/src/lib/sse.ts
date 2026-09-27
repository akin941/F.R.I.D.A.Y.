import type { ChatStreamEvent } from "@/types/api";

const FRAME_BOUNDARY = /\r\n\r\n|\n\n/;

function parseFrame(frame: string): ChatStreamEvent | { type: "protocol_error" } | null {
  const dataLines = frame
    .split(/\r\n|\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""));
  if (dataLines.length === 0) {
    return null;
  }
  try {
    const payload = JSON.parse(dataLines.join("\n"));
    if (payload && typeof payload === "object" && typeof payload.type === "string") {
      if (payload.type === "chunk" && typeof payload.text === "string") {
        return { type: "chunk", text: payload.text };
      }
      if (payload.type === "done" && typeof payload.message_id === "string") {
        const memoriesUsed = Array.isArray(payload.memories_used)
          ? payload.memories_used.filter((id: unknown): id is string => typeof id === "string")
          : [];
        const chunksUsed = Array.isArray(payload.chunks_used)
          ? payload.chunks_used.filter((id: unknown): id is string => typeof id === "string")
          : [];
        const overlaySourceWorkspaces =
          payload.overlay_source_workspaces !== null &&
          typeof payload.overlay_source_workspaces === "object"
            ? payload.overlay_source_workspaces
            : {};
        return {
          type: "done",
          message_id: payload.message_id,
          usage: payload.usage,
          memories_used: memoriesUsed,
          chunks_used: chunksUsed,
          overlay_source_workspaces: overlaySourceWorkspaces,
        };
      }
      if (payload.type === "error" && typeof payload.error_type === "string") {
        return { type: "error", error_type: payload.error_type };
      }
      if (
        payload.type === "draft" &&
        payload.kind === "task" &&
        typeof payload.source_message_id === "string" &&
        typeof payload.title === "string" &&
        (payload.due_date === null || typeof payload.due_date === "string")
      ) {
        return {
          type: "draft",
          kind: "task",
          source_message_id: payload.source_message_id,
          title: payload.title,
          due_date: payload.due_date,
        };
      }
      if (
        payload.type === "draft" &&
        payload.kind === "tool" &&
        typeof payload.source_message_id === "string" &&
        typeof payload.tool_name === "string" &&
        payload.payload !== null &&
        typeof payload.payload === "object"
      ) {
        return {
          type: "draft",
          kind: "tool",
          source_message_id: payload.source_message_id,
          tool_name: payload.tool_name,
          payload: payload.payload,
        };
      }
      // Unknown event type: ignore without crashing.
      return null;
    }
    return { type: "protocol_error" };
  } catch {
    return { type: "protocol_error" };
  }
}

/**
 * Incrementally decodes a byte stream into chat SSE events. A persistent
 * TextDecoder is required because a multi-byte UTF-8 character can be split
 * across two network chunks; decoding each chunk independently would corrupt
 * it. Incomplete frames are buffered until the next `\n\n`/`\r\n\r\n`.
 */
export class SSEStreamParser {
  private decoder = new TextDecoder();
  private buffer = "";

  push(chunk: Uint8Array): ChatStreamEvent[] {
    this.buffer += this.decoder.decode(chunk, { stream: true });
    return this.drain();
  }

  private drain(): ChatStreamEvent[] {
    const events: ChatStreamEvent[] = [];
    let match: RegExpMatchArray | null;
    while ((match = this.buffer.match(FRAME_BOUNDARY))) {
      const boundaryIndex = match.index ?? 0;
      const frame = this.buffer.slice(0, boundaryIndex);
      this.buffer = this.buffer.slice(boundaryIndex + match[0].length);
      const parsed = parseFrame(frame);
      if (parsed === null) {
        continue;
      }
      if (parsed.type === "protocol_error") {
        events.push({ type: "error", error_type: "protocol_error" });
        continue;
      }
      events.push(parsed);
    }
    return events;
  }
}
