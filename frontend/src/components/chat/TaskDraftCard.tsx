"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiTask } from "@/types/api";

export interface TaskDraft {
  sourceMessageId: string;
  title: string;
  dueDate: string | null;
}

/**
 * Confirm/discard card for a `task_draft`-classified chat turn (MVP 6 §3).
 * Confirm reuses the existing `POST /api/tasks` route -- the only new
 * thing sent is `draft_message_id`, a REFERENCE the server independently
 * verifies (same actor, same workspace, `intent="task_draft"`) before ever
 * marking the created row `ai_suggested`; this component never asserts
 * that provenance itself. Discard is a pure client-side no-op -- nothing
 * was created, so there is nothing to undo.
 */
export function TaskDraftCard({
  draft,
  workspaceId,
  onConfirmed,
  onDiscard,
}: {
  draft: TaskDraft;
  workspaceId: string;
  onConfirmed: (task: ApiTask) => void;
  onDiscard: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm() {
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
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "That task could not be created.")
          : "Something went wrong. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="m-4" data-testid="task-draft-card">
      <CardHeader>
        <CardTitle>Looks like a task</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <p className="text-sm font-medium">{draft.title}</p>
        {draft.dueDate ? (
          <p className="text-sm text-muted-foreground">Due {draft.dueDate}</p>
        ) : null}
      </CardContent>
      <CardFooter className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onDiscard} disabled={busy}>
          Discard
        </Button>
        <Button size="sm" onClick={() => void handleConfirm()} disabled={busy}>
          {busy ? "Creating…" : "Confirm task"}
        </Button>
      </CardFooter>
    </Card>
  );
}
