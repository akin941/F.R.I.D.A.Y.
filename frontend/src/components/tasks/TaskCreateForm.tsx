"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { TaskFields, type TaskFieldsValue } from "@/components/tasks/TaskFields";
import { apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiTask } from "@/types/api";

const EMPTY: TaskFieldsValue = {
  title: "",
  description: "",
  due_date: "",
  priority: "normal",
};

export function TaskCreateForm({
  workspaceId,
  onCreated,
  onCancel,
}: {
  workspaceId: string;
  onCreated: (task: ApiTask) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState<TaskFieldsValue>(EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const task = await apiPostJson<ApiTask>("/api/tasks", {
        workspace: workspaceId,
        title: value.title,
        description: value.description,
        priority: value.priority,
        due_date: value.due_date || null,
      });
      onCreated(task);
      setValue(EMPTY);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.body.detail ?? "Görev oluşturulamadı.");
      } else {
        setError("Bir şeyler ters gitti. Lütfen tekrar deneyin.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Yeni görev</CardTitle>
      </CardHeader>
      <form onSubmit={handleSubmit}>
        <CardContent className="flex flex-col gap-3">
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <TaskFields value={value} onChange={setValue} idPrefix="create-task" />
        </CardContent>
        <CardFooter className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
            İptal
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? "Oluşturuluyor…" : "Görev oluştur"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
