"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  KNOWLEDGE_SENSITIVITY_LABELS,
  KNOWLEDGE_SOURCE_TYPE_LABELS,
  KNOWLEDGE_STATUS_LABELS,
} from "@/components/knowledge/labels";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useWorkspace } from "@/contexts/workspace-context";
import { TRASH_RETENTION_DAYS_DEFAULT } from "@/lib/trash";
import { apiDelete } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import type { ApiKnowledgeDocument } from "@/types/api";

export function KnowledgeCard({
  document,
  onDeleted,
}: {
  document: ApiKnowledgeDocument;
  onDeleted: (id: string) => void;
}) {
  const trashRetentionDays =
    useWorkspace().trashRetentionDays ?? TRASH_RETENTION_DAYS_DEFAULT;

  // Runs only from the confirmation dialog, which reports a failure.
  async function handleDelete() {
    await apiDelete(`/api/knowledge/documents/${document.id}`);
    onDeleted(document.id);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{document.title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
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
        <ConfirmDialog
          trigger={
            <Button variant="destructive" size="sm">
              Sil
            </Button>
          }
          title={tr.confirm.trash.documentTitle}
          description={tr.confirm.trash.document(document.title, trashRetentionDays)}
          confirmLabel={tr.confirm.trash.confirm}
          busyLabel={tr.confirm.trash.deleting}
          errorFallback={tr.confirm.trash.documentFailed}
          onConfirm={handleDelete}
        />
      </CardFooter>
    </Card>
  );
}
