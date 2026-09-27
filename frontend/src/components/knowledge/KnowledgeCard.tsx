"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  KNOWLEDGE_SENSITIVITY_LABELS,
  KNOWLEDGE_SOURCE_TYPE_LABELS,
  KNOWLEDGE_STATUS_LABELS,
} from "@/components/knowledge/labels";
import { apiDelete, errorMessage } from "@/lib/api-client";
import type { ApiKnowledgeDocument } from "@/types/api";

export function KnowledgeCard({
  document,
  onDeleted,
}: {
  document: ApiKnowledgeDocument;
  onDeleted: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDelete() {
    setBusy(true);
    setError(null);
    try {
      await apiDelete(`/api/knowledge/documents/${document.id}`);
      onDeleted(document.id);
    } catch (err) {
      setError(
        errorMessage(err, "Belge silinemedi."),
      );
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{document.title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        {document.status === "failed" && document.error_message ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{document.error_message}</AlertDescription>
          </Alert>
        ) : null}
        {document.source_type === "url" && document.source_url ? (
          <p className="truncate text-sm text-muted-foreground">{document.source_url}</p>
        ) : null}
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{KNOWLEDGE_SOURCE_TYPE_LABELS[document.source_type]}</span>
          <span>&middot;</span>
          <span>{KNOWLEDGE_STATUS_LABELS[document.status]}</span>
          <span>&middot;</span>
          <span>{KNOWLEDGE_SENSITIVITY_LABELS[document.sensitivity]}</span>
        </div>
      </CardContent>
      <CardFooter className="flex justify-end gap-2">
        <Button
          variant="destructive"
          size="sm"
          onClick={() => void handleDelete()}
          disabled={busy}
        >
          Sil
        </Button>
      </CardFooter>
    </Card>
  );
}
