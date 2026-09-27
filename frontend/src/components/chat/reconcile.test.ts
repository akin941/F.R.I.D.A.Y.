import { describe, expect, it } from "vitest";
import { isTurnSettled, mergeTurn, type LiveTurn } from "@/components/chat/reconcile";
import type { ApiMessage } from "@/types/api";

function row(id: string, role: "user" | "assistant", content = id, status = "complete"): ApiMessage {
  return {
    id,
    conversation: "c1",
    role,
    content,
    status: status as ApiMessage["status"],
    model_usage: null,
    created_by: null,
    created_at: "t",
    memories_used: [],
    chunks_used: [],
    overlay_source_workspaces: {},
    intent: "",
    intent_payload: {},
    channel: "web",
    confirmed_task: null,
    chunk_documents: {},
  };
}

const EARLIER = [row("u0", "user"), row("a0", "assistant")];

function turn(overrides: Partial<LiveTurn> = {}): LiveTurn {
  return {
    id: "local-1",
    conversationId: "c1",
    baselineIds: new Set(EARLIER.map((m) => m.id)),
    content: "Soru",
    assistantText: "Yarım ya",
    phase: "streaming",
    doneMessageId: null,
    ...overrides,
  };
}

describe("mergeTurn", () => {
  it("shows both local bubbles while the server has neither row", () => {
    const views = mergeTurn(EARLIER, turn());
    expect(views.map((v) => [v.id, v.status])).toEqual([
      ["u0", "complete"],
      ["a0", "complete"],
      ["local-1-user", "complete"],
      ["local-1-assistant", "streaming"],
    ]);
  });

  it("drops the optimistic user bubble once any new user row is persisted", () => {
    const views = mergeTurn([...EARLIER, row("u1", "user", "Soru")], turn());
    expect(views.map((v) => v.id)).toEqual(["u0", "a0", "u1", "local-1-assistant"]);
  });

  it("marks an interrupted local reply partial (with text) or failed (without)", () => {
    expect(mergeTurn(EARLIER, turn({ phase: "interrupted" })).at(-1)?.status).toBe("partial");
    expect(
      mergeTurn(EARLIER, turn({ phase: "interrupted", assistantText: "" })).at(-1)?.status,
    ).toBe("failed");
    expect(mergeTurn(EARLIER, turn({ phase: "done" })).at(-1)?.status).toBe("complete");
  });

  it("reconciles by id: after `done` only the named assistant row settles the turn", () => {
    const done = turn({ phase: "done", doneMessageId: "a1" });
    // A new assistant row with another id (e.g. from another tab) is not ours.
    const other = [...EARLIER, row("u1", "user"), row("a-other", "assistant")];
    expect(isTurnSettled(other, done)).toBe(false);
    expect(mergeTurn(other, done).at(-1)?.id).toBe("local-1-assistant");

    const settled = [...EARLIER, row("u1", "user"), row("a1", "assistant", "Yarım ya")];
    expect(isTurnSettled(settled, done)).toBe(true);
    expect(mergeTurn(settled, done).map((v) => v.id)).toEqual(["u0", "a0", "u1", "a1"]);
  });

  it("after an abort, any new assistant row is the turn's persisted partial", () => {
    const aborted = turn({ phase: "interrupted" });
    const server = [...EARLIER, row("u1", "user"), row("a1", "assistant", "Yarım ya", "partial")];
    expect(isTurnSettled(server, aborted)).toBe(true);
    const views = mergeTurn(server, aborted);
    expect(views.filter((v) => v.local)).toEqual([]);
    expect(views.at(-1)).toMatchObject({ id: "a1", status: "partial" });
  });

  it("with no live turn the history renders as is", () => {
    expect(mergeTurn(EARLIER, null).map((v) => v.id)).toEqual(["u0", "a0"]);
  });
});
