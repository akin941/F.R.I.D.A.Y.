"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { MemoryFields, type MemoryFieldsValue } from "@/components/memory/MemoryFields";
import { apiPostJson, errorMessage } from "@/lib/api-client";
import type { ApiMemory } from "@/types/api";

const EMPTY: MemoryFieldsValue = {
  title: "",
  content: "",
  memory_type: "semantic",
  sensitivity: "normal",
};

export function MemoryCreateForm({
  workspaceId,
  onCreated,
  onCancel,
}: {
  workspaceId: string;
  onCreated: (memory: ApiMemory) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState<MemoryFieldsValue>(EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const memory = await apiPostJson<ApiMemory>("/api/memories", {
        workspace: workspaceId,
        ...value,
      });
      onCreated(memory);
      setValue(EMPTY);
    } catch (err) {
      setError(errorMessage(err, "Not oluşturulamadı."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Yeni not</CardTitle>
      </CardHeader>
      <form onSubmit={handleSubmit}>
        <CardContent className="flex flex-col gap-3">
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <MemoryFields value={value} onChange={setValue} idPrefix="create-memory" />
        </CardContent>
        <CardFooter className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
            İptal
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? "Oluşturuluyor…" : "Not oluştur"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
