import { afterEach, describe, expect, it } from "vitest";
import {
  dismissDraft,
  loadDismissedDrafts,
  taskDraftFromMessage,
  toolDraftFromMessage,
} from "@/components/chat/drafts";
import type { ChatMessageView } from "@/components/chat/types";

function user(overrides: Partial<ChatMessageView>): ChatMessageView {
  return { id: "u1", role: "user", content: "x", status: "complete", channel: "web", ...overrides };
}

describe("draft reconstruction", () => {
  it("rebuilds a task draft from a web-channel task_draft message", () => {
    expect(
      taskDraftFromMessage(
        user({ intent: "task_draft", intentPayload: { title: "Annemi ara", due_date: "2026-07-15" } }),
      ),
    ).toEqual({ sourceMessageId: "u1", title: "Annemi ara", dueDate: "2026-07-15" });
  });

  it("ignores other channels, other intents, assistant rows, local bubbles and bad payloads", () => {
    const payload = { title: "Annemi ara", due_date: null };
    expect(taskDraftFromMessage(user({ intent: "task_draft", intentPayload: payload, channel: "telegram" }))).toBeNull();
    expect(taskDraftFromMessage(user({ intent: "chat", intentPayload: payload }))).toBeNull();
    expect(taskDraftFromMessage(user({ intent: "task_draft", intentPayload: payload, role: "assistant" }))).toBeNull();
    expect(taskDraftFromMessage(user({ intent: "task_draft", intentPayload: payload, local: true }))).toBeNull();
    expect(taskDraftFromMessage(user({ intent: "task_draft", intentPayload: { title: "  " } }))).toBeNull();
  });

  it("rebuilds drafts from a live Mac voice turn (node_voice, Phase 11 D4), not from cli/openai", () => {
    const payload = { title: "Annemi ara", due_date: null };
    expect(
      taskDraftFromMessage(user({ intent: "task_draft", intentPayload: payload, channel: "node_voice" })),
    ).toEqual({ sourceMessageId: "u1", title: "Annemi ara", dueDate: null });
    expect(
      toolDraftFromMessage(
        user({ intent: "tool_request", intentPayload: { tool_name: "node_action", payload: {} }, channel: "node_voice" }),
      ),
    ).toEqual({ sourceMessageId: "u1", toolName: "node_action", payload: {} });
    for (const channel of ["cli", "openai"] as const) {
      expect(taskDraftFromMessage(user({ intent: "task_draft", intentPayload: payload, channel }))).toBeNull();
      expect(
        toolDraftFromMessage(user({ intent: "tool_request", intentPayload: { tool_name: "x" }, channel })),
      ).toBeNull();
    }
    expect(taskDraftFromMessage(user({ intent: "task_draft", intentPayload: payload, channel: undefined }))).toBeNull();
  });

  it("rebuilds a tool request draft, defaulting a malformed payload to {}", () => {
    expect(
      toolDraftFromMessage(
        user({ intent: "tool_request", intentPayload: { tool_name: "vault_export", payload: [] } }),
      ),
    ).toEqual({ sourceMessageId: "u1", toolName: "vault_export", payload: {} });
    expect(toolDraftFromMessage(user({ intent: "tool_request", intentPayload: {} }))).toBeNull();
  });
});

describe("dismissed drafts", () => {
  afterEach(() => window.localStorage.clear());

  it("persists dismissals across loads, bounded", () => {
    let set = loadDismissedDrafts();
    expect(set.size).toBe(0);
    for (let i = 0; i < 205; i += 1) set = dismissDraft(set, `m${i}`);
    const reloaded = loadDismissedDrafts();
    expect(reloaded.size).toBe(200);
    expect(reloaded.has("m204")).toBe(true);
    expect(reloaded.has("m0")).toBe(false);
  });

  it("survives corrupt storage", () => {
    window.localStorage.setItem("friday:chat:dismissed-drafts", "{not json");
    expect(loadDismissedDrafts().size).toBe(0);
  });

  it("carries pre-rename dismissals over once", () => {
    window.localStorage.removeItem("friday:chat:dismissed-drafts");
    window.localStorage.setItem("jarvis:chat:dismissed-drafts", JSON.stringify(["old1"]));
    expect(loadDismissedDrafts().has("old1")).toBe(true);
    expect(window.localStorage.getItem("jarvis:chat:dismissed-drafts")).toBeNull();
    expect(window.localStorage.getItem("friday:chat:dismissed-drafts")).toBe('["old1"]');
  });
});
