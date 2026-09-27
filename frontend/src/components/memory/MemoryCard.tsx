"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  MEMORY_SENSITIVITY_LABELS,
  MEMORY_STATUS_LABELS,
  MEMORY_TYPE_LABELS,
} from "@/components/memory/labels";
import { MemoryFields, type MemoryFieldsValue } from "@/components/memory/MemoryFields";
import { apiDelete, apiPatchJson, apiPostJson, errorMessage } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import type { ApiMemory } from "@/types/api";

const copy = tr.confirm.memory;

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
      setError(errorMessage(err, "İşlem tamamlanamadı."));
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

  // Reject, forget and delete run only from their confirmation dialog,
  // which reports its own failure (so these throw instead of `run`).
  async function handleReject() {
    onChanged(await apiPostJson<ApiMemory>(`/api/memories/${memory.id}/reject`));
  }

  async function handleForget() {
    onChanged(await apiPostJson<ApiMemory>(`/api/memories/${memory.id}/forget`));
  }

  async function handleRestore() {
    const updated = await run(() => apiPostJson<ApiMemory>(`/api/memories/${memory.id}/restore`));
    if (updated) onChanged(updated);
  }

  async function handleDelete() {
    await apiDelete(`/api/memories/${memory.id}`);
    onDeleted(memory.id);
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
            <ConfirmDialog
              trigger={
                <Button variant="outline" size="sm" disabled={busy}>
                  Reddet
                </Button>
              }
              title={copy.rejectTitle}
              description={copy.reject(memory.title)}
              confirmLabel={copy.rejectConfirm}
              busyLabel={copy.rejecting}
              errorFallback={copy.failed}
              onConfirm={handleReject}
            />
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
            <ConfirmDialog
              trigger={
                <Button variant="outline" size="sm" disabled={busy}>
                  Unut
                </Button>
              }
              title={copy.forgetTitle}
              description={copy.forget(memory.title)}
              confirmLabel={copy.forgetConfirm}
              busyLabel={copy.forgetting}
              errorFallback={copy.failed}
              onConfirm={handleForget}
            />
          </>
        ) : null}
        {variant === "list" && memory.status === "archived" ? (
          <Button variant="outline" size="sm" onClick={() => void handleRestore()} disabled={busy}>
            Geri yükle
          </Button>
        ) : null}
        {variant === "list" && memory.status === "rejected" ? (
          <ConfirmDialog
            trigger={
              <Button variant="destructive" size="sm" disabled={busy}>
                Sil
              </Button>
            }
            title={copy.deleteTitle}
            description={copy.delete(memory.title)}
            confirmLabel={copy.deleteConfirm}
            errorFallback={copy.failed}
            onConfirm={handleDelete}
          />
        ) : null}
      </CardFooter>
    </Card>
  );
}
