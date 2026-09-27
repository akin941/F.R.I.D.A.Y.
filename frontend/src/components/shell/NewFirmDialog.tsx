"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiWorkspaceDetail } from "@/types/api";

/**
 * "Yeni firma" (Phase 4 branch 2): the owner creates a firm (company)
 * workspace; the workspace context then refreshes and switches to it.
 * Deleting/archiving a firm is out of scope (gate-class).
 */
export function NewFirmDialog() {
  const { refresh, setActiveWorkspaceId } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setNotes("");
    setError(null);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const created = await apiPostJson<ApiWorkspaceDetail>("/api/workspaces", {
        name: name.trim(),
        ...(notes.trim() ? { profile: { notes: notes.trim() } } : {}),
      });
      await refresh();
      setActiveWorkspaceId(created.id);
      setOpen(false);
      reset();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "Firma oluşturulamadı.")
          : "Bir şeyler ters gitti. Lütfen tekrar dene.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="w-full">
          Yeni firma
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Yeni firma</DialogTitle>
          <DialogDescription>
            Firma için ayrı bir çalışma alanı: hafızası, bilgisi, görevleri ve onayları diğerlerinden
            ayrı tutulur.
          </DialogDescription>
        </DialogHeader>
        <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <div className="flex flex-col gap-2">
            <Label htmlFor="firm-name">Firma adı</Label>
            <Input
              id="firm-name"
              maxLength={255}
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="firm-notes">Notlar (isteğe bağlı)</Label>
            <Textarea id="firm-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>
          <Button type="submit" disabled={submitting || !name.trim()}>
            {submitting ? "Oluşturuluyor…" : "Oluştur"}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
