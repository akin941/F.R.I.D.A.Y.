"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  MEMORY_SENSITIVITY_LABELS,
  MEMORY_STATUS_LABELS,
  MEMORY_TYPE_LABELS,
} from "@/components/memory/labels";
import { MemoryFields, type MemoryFieldsValue } from "@/components/memory/MemoryFields";
import { apiDelete, apiPatchJson, apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiMemory } from "@/types/api";

function toFieldsValue(memory: ApiMemory): MemoryFieldsValue {
  return {
    title: memory.title,
    content: memory.content,
    memory_type: memory.memory_type,
    sensitivity: memory.sensitivity,
  };
}

export function MemoryCard({
  memory,
  onChanged,
  onDeleted,
  variant = "list",
}: {
  memory: ApiMemory;
  onChanged: (memory: ApiMemory) => void;
  onDeleted: (id: string) => void;
  /** "pending" shows Approve/Reject; "list" shows the status-appropriate actions. */
  variant?: "pending" | "list";
}) {
  const [editing, setEditing] = useState(false);
  const [fields, setFields] = useState<MemoryFieldsValue>(toFieldsValue(memory));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run<T>(action: () => Promise<T>) {
    setBusy(true);
    setError(null);
    try {
      return await action();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "İşlem tamamlanamadı.")
          : "Bir şeyler ters gitti. Lütfen tekrar deneyin.",
      );
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveEdit(event: React.FormEvent) {
    event.preventDefault();
    const updated = await run(() => apiPatchJson<ApiMemory>(`/api/memories/${memory.id}`, fields));
    if (updated) {
      onChanged(updated);
      setEditing(false);
    }
  }

  async function handleApprove() {
    const updated = await run(() =>
      apiPostJson<ApiMemory>(`/api/memories/${memory.id}/approve`),
    );
    if (updated) onChanged(updated);
  }

  async function handleReject() {
    const updated = await run(() => apiPostJson<ApiMemory>(`/api/memories/${memory.id}/reject`));
    if (updated) onChanged(updated);
  }

  async function handleForget() {
    const updated = await run(() => apiPostJson<ApiMemory>(`/api/memories/${memory.id}/forget`));
    if (updated) onChanged(updated);
  }

  async function handleRestore() {
    const updated = await run(() => apiPostJson<ApiMemory>(`/api/memories/${memory.id}/restore`));
    if (updated) onChanged(updated);
  }

  async function handleDelete() {
    await run(async () => {
      await apiDelete(`/api/memories/${memory.id}`);
      onDeleted(memory.id);
    });
  }

  if (editing) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Notu düzenle</CardTitle>
        </CardHeader>
        <form onSubmit={handleSaveEdit}>
          <CardContent className="flex flex-col gap-3">
            {error ? (
              <Alert variant="destructive" role="alert">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            <MemoryFields value={fields} onChange={setFields} idPrefix={`edit-${memory.id}`} />
          </CardContent>
          <CardFooter className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setFields(toFieldsValue(memory));
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
        <CardTitle>{memory.title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <p className="whitespace-pre-wrap text-sm">{memory.content}</p>
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{MEMORY_TYPE_LABELS[memory.memory_type]}</span>
          <span>&middot;</span>
          <span>{MEMORY_SENSITIVITY_LABELS[memory.sensitivity]}</span>
          {variant === "list" ? (
            <>
              <span>&middot;</span>
              <span>{MEMORY_STATUS_LABELS[memory.status]}</span>
            </>
          ) : null}
        </div>
      </CardContent>
      <CardFooter className="flex flex-wrap justify-end gap-2">
        {variant === "pending" ? (
          <>
            <Button variant="outline" size="sm" onClick={() => setEditing(true)} disabled={busy}>
              Düzenle
            </Button>
            <Button variant="outline" size="sm" onClick={() => void handleReject()} disabled={busy}>
              Reddet
            </Button>
            <Button size="sm" onClick={() => void handleApprove()} disabled={busy}>
              Onayla
            </Button>
          </>
        ) : null}
        {variant === "list" && memory.status === "approved" ? (
          <>
            <Button variant="outline" size="sm" onClick={() => setEditing(true)} disabled={busy}>
              Düzenle
            </Button>
            <Button variant="outline" size="sm" onClick={() => void handleForget()} disabled={busy}>
              Unut
            </Button>
          </>
        ) : null}
        {variant === "list" && memory.status === "archived" ? (
          <Button variant="outline" size="sm" onClick={() => void handleRestore()} disabled={busy}>
            Geri yükle
          </Button>
        ) : null}
        {variant === "list" && memory.status === "rejected" ? (
          <Button variant="destructive" size="sm" onClick={() => void handleDelete()} disabled={busy}>
            Sil
          </Button>
        ) : null}
      </CardFooter>
    </Card>
  );
}
