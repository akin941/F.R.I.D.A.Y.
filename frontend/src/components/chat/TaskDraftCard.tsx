"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { apiPostJson, errorMessage } from "@/lib/api-client";
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
      setError(errorMessage(err, "Görev oluşturulamadı."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="m-4" data-testid="task-draft-card">
      <CardHeader>
        <CardTitle>Bu bir görev gibi görünüyor</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <p className="text-sm font-medium">{draft.title}</p>
        {draft.dueDate ? (
          <p className="text-sm text-muted-foreground">Son tarih {draft.dueDate}</p>
        ) : null}
      </CardContent>
      <CardFooter className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onDiscard} disabled={busy}>
          Vazgeç
        </Button>
        <Button size="sm" onClick={() => void handleConfirm()} disabled={busy}>
          {busy ? "Oluşturuluyor…" : "Görevi onayla"}
        </Button>
      </CardFooter>
    </Card>
  );
}
