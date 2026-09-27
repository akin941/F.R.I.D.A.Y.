import { describe, expect, it } from "vitest";
import { SSEStreamParser } from "@/lib/sse";

const encoder = new TextEncoder();

function frame(payload: unknown): Uint8Array {
  return encoder.encode(`data: ${JSON.stringify(payload)}\n\n`);
}

describe("SSEStreamParser", () => {
  it("parses a frame split across multiple network chunks", () => {
    const parser = new SSEStreamParser();
    const full = `data: ${JSON.stringify({ type: "chunk", text: "hello" })}\n\n`;
    const bytes = encoder.encode(full);
    const first = parser.push(bytes.slice(0, 10));
    const second = parser.push(bytes.slice(10));
    expect(first).toEqual([]);
    expect(second).toEqual([{ type: "chunk", text: "hello" }]);
  });

  it("parses multiple events delivered in a single chunk", () => {
    const parser = new SSEStreamParser();
    const combined = new Uint8Array([
      ...frame({ type: "chunk", text: "a" }),
      ...frame({ type: "chunk", text: "b" }),
    ]);
    const events = parser.push(combined);
    expect(events).toEqual([
      { type: "chunk", text: "a" },
      { type: "chunk", text: "b" },
    ]);
  });

  it("reassembles a multi-byte UTF-8 character split across chunks", () => {
    const parser = new SSEStreamParser();
    const bytes = frame({ type: "chunk", text: "café 🚀" });
    // Split in the middle of the multi-byte emoji sequence.
    const splitPoint = bytes.length - 2;
    const first = parser.push(bytes.slice(0, splitPoint));
    const second = parser.push(bytes.slice(splitPoint));
    expect(first).toEqual([]);
    expect(second).toEqual([{ type: "chunk", text: "café 🚀" }]);
  });

  it("parses a chunk followed by done", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(
      new Uint8Array([
        ...frame({ type: "chunk", text: "hi" }),
        ...frame({ type: "done", message_id: "m1", usage: { tokens: 3 } }),
      ]),
    );
    expect(events).toEqual([
      { type: "chunk", text: "hi" },
      {
        type: "done",
        message_id: "m1",
        usage: { tokens: 3 },
        memories_used: [],
        chunks_used: [],
        overlay_source_workspaces: {},
      },
    ]);
  });

  it("parses memories_used on a done event", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(
      frame({ type: "done", message_id: "m1", memories_used: ["mem-1", "mem-2"] }),
    );
    expect(events).toEqual([
      {
        type: "done",
        message_id: "m1",
        usage: undefined,
        memories_used: ["mem-1", "mem-2"],
        chunks_used: [],
        overlay_source_workspaces: {},
      },
    ]);
  });

  it("defaults memories_used to an empty array when the field is missing or malformed", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(frame({ type: "done", message_id: "m1", memories_used: "not-an-array" }));
    expect(events).toEqual([
      {
        type: "done",
        message_id: "m1",
        usage: undefined,
        memories_used: [],
        chunks_used: [],
        overlay_source_workspaces: {},
      },
    ]);
  });

  it("parses chunks_used on a done event", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(
      frame({ type: "done", message_id: "m1", chunks_used: ["chunk-1", "chunk-2"] }),
    );
    expect(events).toEqual([
      {
        type: "done",
        message_id: "m1",
        usage: undefined,
        memories_used: [],
        chunks_used: ["chunk-1", "chunk-2"],
        overlay_source_workspaces: {},
      },
    ]);
  });

  it("defaults chunks_used to an empty array when the field is missing or malformed", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(frame({ type: "done", message_id: "m1", chunks_used: "not-an-array" }));
    expect(events).toEqual([
      {
        type: "done",
        message_id: "m1",
        usage: undefined,
        memories_used: [],
        chunks_used: [],
        overlay_source_workspaces: {},
      },
    ]);
  });

  it("parses overlay_source_workspaces on a done event", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(
      frame({
        type: "done",
        message_id: "m1",
        memories_used: ["mem-1"],
        overlay_source_workspaces: { "mem-1": "ws-1" },
      }),
    );
    expect(events).toEqual([
      {
        type: "done",
        message_id: "m1",
        usage: undefined,
        memories_used: ["mem-1"],
        chunks_used: [],
        overlay_source_workspaces: { "mem-1": "ws-1" },
      },
    ]);
  });

  it("defaults overlay_source_workspaces to an empty object when the field is missing or malformed", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(
      frame({ type: "done", message_id: "m1", overlay_source_workspaces: "not-an-object" }),
    );
    expect(events).toEqual([
      {
        type: "done",
        message_id: "m1",
        usage: undefined,
        memories_used: [],
        chunks_used: [],
        overlay_source_workspaces: {},
      },
    ]);
  });

  it("parses a chunk followed by error", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(
      new Uint8Array([
        ...frame({ type: "chunk", text: "hi" }),
        ...frame({ type: "error", error_type: "no_provider" }),
      ]),
    );
    expect(events).toEqual([
      { type: "chunk", text: "hi" },
      { type: "error", error_type: "no_provider" },
    ]);
  });

  it("buffers an incomplete frame instead of emitting a partial event", () => {
    const parser = new SSEStreamParser();
    const bytes = frame({ type: "chunk", text: "incomplete" });
    const events = parser.push(bytes.slice(0, bytes.length - 1));
    expect(events).toEqual([]);
  });

  it("surfaces malformed JSON as a protocol error instead of throwing", () => {
    const parser = new SSEStreamParser();
    const malformed = encoder.encode("data: {not-json\n\n");
    const events = parser.push(malformed);
    expect(events).toEqual([{ type: "error", error_type: "protocol_error" }]);
  });

  it("parses a chunk followed by draft then done (MVP 6 §3/§5)", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(
      new Uint8Array([
        ...frame({ type: "chunk", text: "hi" }),
        ...frame({
          type: "draft",
          kind: "task",
          source_message_id: "msg-1",
          title: "Call mom",
          due_date: "2026-07-15",
        }),
        ...frame({ type: "done", message_id: "m1" }),
      ]),
    );
    expect(events).toEqual([
      { type: "chunk", text: "hi" },
      {
        type: "draft",
        kind: "task",
        source_message_id: "msg-1",
        title: "Call mom",
        due_date: "2026-07-15",
      },
      {
        type: "done",
        message_id: "m1",
        usage: undefined,
        memories_used: [],
        chunks_used: [],
        overlay_source_workspaces: {},
      },
    ]);
  });

  it("parses a draft event with a null due_date", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(
      frame({
        type: "draft",
        kind: "task",
        source_message_id: "msg-1",
        title: "Call mom",
        due_date: null,
      }),
    );
    expect(events).toEqual([
      {
        type: "draft",
        kind: "task",
        source_message_id: "msg-1",
        title: "Call mom",
        due_date: null,
      },
    ]);
  });

  it("ignores a draft event with a malformed shape without crashing", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(frame({ type: "draft", kind: "task", title: "Call mom" }));
    expect(events).toEqual([]);
  });

  it("ignores unknown event types without crashing", () => {
    const parser = new SSEStreamParser();
    const events = parser.push(frame({ type: "heartbeat" }));
    expect(events).toEqual([]);
  });

  it("supports CRLF frame boundaries", () => {
    const parser = new SSEStreamParser();
    const bytes = encoder.encode(
      `data: ${JSON.stringify({ type: "chunk", text: "crlf" })}\r\n\r\n`,
    );
    const events = parser.push(bytes);
    expect(events).toEqual([{ type: "chunk", text: "crlf" }]);
  });
});
