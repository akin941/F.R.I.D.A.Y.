import { toView, type ChatMessageView } from "@/components/chat/types";
import type { ApiMessage } from "@/types/api";

/**
 * The live turn: what this tab sent and has streamed so far, kept until
 * the server's persisted rows for it are in the history.
 *
 * Reconciliation is by message id, never by position or timing (Phase 4
 * branch 4, stop and recover): `baselineIds` are the ids the history held
 * when the turn was sent, so any row NOT in it belongs to this turn.
 *
 * - The user row is persisted before streaming starts; once any new user
 *   row is in the history, the optimistic bubble is dropped.
 * - The assistant row is written once, when the stream ends -- after a
 *   `done` (its id is known: `doneMessageId`) or, after an abort or a
 *   dropped connection, whenever the server notices and persists the
 *   `partial`/`failed` row. Until a new assistant row shows up, the text
 *   streamed so far stays on screen as a local bubble.
 */
export interface LiveTurn {
  id: string;
  conversationId: string;
  baselineIds: ReadonlySet<string>;
  content: string;
  assistantText: string;
  phase: "streaming" | "done" | "interrupted";
  doneMessageId: string | null;
}

type TurnRef = Pick<LiveTurn, "baselineIds" | "doneMessageId">;

function turnRows(server: ApiMessage[], turn: TurnRef) {
  const fresh = server.filter((m) => !turn.baselineIds.has(m.id));
  const hasUser = fresh.some((m) => m.role === "user");
  const hasAssistant = turn.doneMessageId
    ? server.some((m) => m.id === turn.doneMessageId)
    : fresh.some((m) => m.role === "assistant");
  return { hasUser, hasAssistant };
}

/** True once both of the turn's rows are in the server history. */
export function isTurnSettled(server: ApiMessage[], turn: TurnRef): boolean {
  const { hasUser, hasAssistant } = turnRows(server, turn);
  return hasUser && hasAssistant;
}

export function localUserId(turn: Pick<LiveTurn, "id">): string {
  return `${turn.id}-user`;
}

export function localAssistantId(turn: Pick<LiveTurn, "id">): string {
  return `${turn.id}-assistant`;
}

/** The history to render: server rows, plus whatever of the live turn the
 *  server does not have yet. */
export function mergeTurn(server: ApiMessage[], turn: LiveTurn | null): ChatMessageView[] {
  const views = server.map(toView);
  if (!turn) return views;
  const { hasUser, hasAssistant } = turnRows(server, turn);
  if (!hasUser) {
    views.push({
      id: localUserId(turn),
      role: "user",
      content: turn.content,
      status: "complete",
      channel: "web",
      local: true,
    });
  }
  if (!hasAssistant) {
    views.push({
      id: localAssistantId(turn),
      role: "assistant",
      content: turn.assistantText,
      status:
        turn.phase === "streaming"
          ? "streaming"
          : turn.phase === "done"
            ? "complete"
            : turn.assistantText
              ? "partial"
              : "failed",
      local: true,
    });
  }
  return views;
}
