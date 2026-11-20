import type { ChatMessageView } from "@/components/chat/types";
import { migrateLegacyStorageKey } from "@/lib/legacy-storage";

export interface TaskDraft {
  sourceMessageId: string;
  title: string;
  dueDate: string | null;
}

export interface ToolRequestDraft {
  sourceMessageId: string;
  toolName: string;
  payload: Record<string, unknown>;
}

/**
 * Persisted draft reconstruction (Phase 4 branch 4, carried forward from
 * MVP 9): a reloaded chat re-renders a task/tool draft from the persisted
 * USER message's `intent`/`intent_payload`. Only web-channel turns: a
 * CLI/Telegram turn's draft was offered (or not) on that channel.
 */
export function taskDraftFromMessage(message: ChatMessageView): TaskDraft | null {
  if (message.local || message.role !== "user" || message.channel !== "web") return null;
  if (message.intent !== "task_draft") return null;
  const title = message.intentPayload?.title;
  if (typeof title !== "string" || !title.trim()) return null;
  const due = message.intentPayload?.due_date;
  return {
    sourceMessageId: message.id,
    title,
    dueDate: typeof due === "string" ? due : null,
  };
}

export function toolDraftFromMessage(message: ChatMessageView): ToolRequestDraft | null {
  if (message.local || message.role !== "user" || message.channel !== "web") return null;
  if (message.intent !== "tool_request") return null;
  const toolName = message.intentPayload?.tool_name;
  if (typeof toolName !== "string" || !toolName) return null;
  const payload = message.intentPayload?.payload;
  return {
    sourceMessageId: message.id,
    toolName,
    payload:
      payload && typeof payload === "object" && !Array.isArray(payload)
        ? (payload as Record<string, unknown>)
        : {},
  };
}

/**
 * Discarding a draft creates nothing server-side (MVP 6), so "discarded"
 * is a per-browser convenience: remembered in localStorage so a reload does
 * not offer the same draft again. Bounded; a blocked or unavailable storage
 * just means discarded drafts reappear after a reload.
 */
const DISMISSED_KEY = "friday:chat:dismissed-drafts";
const LEGACY_DISMISSED_KEY = "jarvis:chat:dismissed-drafts";
const DISMISSED_LIMIT = 200;

export function loadDismissedDrafts(): Set<string> {
  migrateLegacyStorageKey(DISMISSED_KEY, LEGACY_DISMISSED_KEY);
  try {
    const raw = window.localStorage.getItem(DISMISSED_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : []);
  } catch {
    return new Set();
  }
}

export function dismissDraft(current: ReadonlySet<string>, messageId: string): Set<string> {
  const next = new Set(current);
  next.add(messageId);
  try {
    window.localStorage.setItem(
      DISMISSED_KEY,
      JSON.stringify([...next].slice(-DISMISSED_LIMIT)),
    );
  } catch {
    // Storage unavailable: the dismissal still holds for this session.
  }
  return next;
}
