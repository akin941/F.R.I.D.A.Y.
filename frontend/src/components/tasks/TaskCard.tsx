"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { TASK_PRIORITY_LABELS, TASK_STATUS_LABELS } from "@/components/tasks/labels";
import { TaskFields, type TaskFieldsValue } from "@/components/tasks/TaskFields";
import { apiDelete, apiPatchJson, apiPostJson, errorMessage } from "@/lib/api-client";
import type { ApiTask } from "@/types/api";

function toFieldsValue(task: ApiTask): TaskFieldsValue {
  return {
    title: task.title,
    description: task.description,
    due_date: task.due_date ?? "",
    priority: task.priority,
  };
}

export function TaskCard({
  task,
  onChanged,
  onDeleted,
}: {
  task: ApiTask;
  onChanged: (task: ApiTask) => void;
  onDeleted: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [fields, setFields] = useState<TaskFieldsValue>(toFieldsValue(task));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run<T>(action: () => Promise<T>) {
    setBusy(true);
    setError(null);
    try {
      return await action();
    } catch (err) {
      setError(
        errorMessage(err, "İşlem tamamlanamadı."),
      );
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveEdit(event: React.FormEvent) {
    event.preventDefault();
    const updated = await run(() =>
      apiPatchJson<ApiTask>(`/api/tasks/${task.id}`, {
        title: fields.title,
        description: fields.description,
        priority: fields.priority,
        due_date: fields.due_date || null,
      }),
    );
    if (updated) {
      onChanged(updated);
      setEditing(false);
    }
  }

  async function handleTransition(action: "complete" | "cancel" | "reopen") {
    const updated = await run(() => apiPostJson<ApiTask>(`/api/tasks/${task.id}/${action}`));
    if (updated) onChanged(updated);
  }

  async function handleDelete() {
    await run(async () => {
      await apiDelete(`/api/tasks/${task.id}`);
      onDeleted(task.id);
    });
  }

  if (editing) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Görevi düzenle</CardTitle>
        </CardHeader>
        <form onSubmit={handleSaveEdit}>
          <CardContent className="flex flex-col gap-3">
            {error ? (
              <Alert variant="destructive" role="alert">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            <TaskFields value={fields} onChange={setFields} idPrefix={`edit-${task.id}`} />
          </CardContent>
          <CardFooter className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setFields(toFieldsValue(task));
                setEditing(false);
              }}
              disabled={busy}
            >
              İptal
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? "Kaydediliyor…" : "Kaydet"}
            </Button>
          </CardFooter>
        </form>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{task.title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        {task.description ? (
          <p className="whitespace-pre-wrap text-sm">{task.description}</p>
        ) : null}
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{TASK_STATUS_LABELS[task.status]}</span>
          <span>&middot;</span>
          <span>{TASK_PRIORITY_LABELS[task.priority]}</span>
          {task.due_date ? (
            <>
              <span>&middot;</span>
              <span>Son tarih {task.due_date}</span>
            </>
          ) : null}
        </div>
      </CardContent>
      <CardFooter className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" size="sm" onClick={() => setEditing(true)} disabled={busy}>
          Düzenle
        </Button>
        {task.status === "open" ? (
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleTransition("cancel")}
              disabled={busy}
            >
              İptal et
            </Button>
            <Button size="sm" onClick={() => void handleTransition("complete")} disabled={busy}>
              Tamamla
            </Button>
          </>
        ) : (
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleTransition("reopen")}
            disabled={busy}
          >
            Yeniden aç
          </Button>
        )}
        <Button variant="destructive" size="sm" onClick={() => void handleDelete()} disabled={busy}>
          Sil
        </Button>
      </CardFooter>
    </Card>
  );
}
