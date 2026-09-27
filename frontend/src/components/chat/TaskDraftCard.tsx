"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { CircleCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type { TaskDraft } from "@/components/chat/drafts";
import { ApiError, apiPostJson, errorMessage } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import type { ApiTask } from "@/types/api";

export type { TaskDraft };

export function taskHref(workspaceId: string, taskId: string): string {
  return `/tasks?workspace=${encodeURIComponent(workspaceId)}&task=${encodeURIComponent(taskId)}`;
}

/**
 * Confirm/discard card for a `task_draft`-classified chat turn (MVP 6 §3),
 * live from the stream or rebuilt from the persisted message (Phase 4
 * branch 4). Confirm reuses `POST /api/tasks` -- the only new thing sent is
 * `draft_message_id`, a REFERENCE the server independently verifies (same
 * actor, same workspace, `intent="task_draft"`) before marking the row
 * `ai_suggested` and linking it as `Task.source_message`. That link is
 * unique, so a second confirm (another tab, a reload) is refused with
 * `task.draft_already_confirmed`; the card then asks for a refresh and
 * shows the confirmed state. Discard is client-side only -- nothing was
 * created, so there is nothing to undo.
 */
export function TaskDraftCard({
  draft,
  workspaceId,
  confirmedTaskId,
  onConfirmed,
  onAlreadyConfirmed,
  onDiscard,
}: {
  draft: TaskDraft;
  workspaceId: string;
  confirmedTaskId: string | null;
  onConfirmed: (task: ApiTask) => void;
  onAlreadyConfirmed: () => void;
  onDiscard: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Same-tick double-click guard (state updates are batched).
  const busyRef = useRef(false);

  if (confirmedTaskId) {
    return (
      <div
        data-testid="task-draft-confirmed"
        className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-success/30 bg-success-muted px-3 py-2 text-sm"
      >
        <CircleCheck aria-hidden className="size-4 shrink-0 text-success" />
        <span className="font-medium">{tr.chat.taskDraft.created}</span>
        <span className="min-w-0 truncate text-muted-foreground">{draft.title}</span>
        <Link
          href={taskHref(workspaceId, confirmedTaskId)}
          className="ml-auto text-primary underline underline-offset-4"
        >
          {tr.chat.taskDraft.openTask}
        </Link>
      </div>
    );
  }

  async function handleConfirm() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      const task = await apiPostJson<ApiTask>("/api/tasks", {
        workspace: workspaceId,
        title: draft.title,
        due_date: draft.dueDate,
        draft_message_id: draft.sourceMessageId,
      });
      onConfirmed(task);
    } catch (err) {
      setError(errorMessage(err, tr.chat.taskDraft.failed));
      if (err instanceof ApiError && err.code === "task.draft_already_confirmed") {
        onAlreadyConfirmed();
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  return (
    <Card size="sm" data-testid="task-draft-card">
      <CardHeader>
        <CardTitle>{tr.chat.taskDraft.title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <p className="text-sm font-medium">{draft.title}</p>
        {draft.dueDate ? (
          <p className="text-sm text-muted-foreground">{tr.chat.taskDraft.due(draft.dueDate)}</p>
        ) : null}
      </CardContent>
      <CardFooter className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onDiscard} disabled={busy}>
          {tr.chat.taskDraft.discard}
        </Button>
        <Button size="sm" onClick={() => void handleConfirm()} disabled={busy}>
          {busy ? tr.chat.taskDraft.confirming : tr.chat.taskDraft.confirm}
        </Button>
      </CardFooter>
    </Card>
  );
}
