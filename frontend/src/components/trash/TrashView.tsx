"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { approvalHref } from "@/components/approvals/links";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson, apiPostJson, errorMessage } from "@/lib/api-client";
import { formatDateTime, formatRelativeTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { TRASH_RETENTION_DAYS_DEFAULT, purgeDate } from "@/lib/trash";
import type { ApiApprovalRequest, ApiKnowledgeDocument, ApiTask } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

const copy = tr.trash;

/** `purge_trash`'s per-request cap (backend tools/purge.py MAX_ITEMS). */
export const PURGE_MAX_ITEMS = 200;

type Kind = "task" | "document";

interface TrashItem {
  kind: Kind;
  id: string;
  title: string;
  deletedAt: string;
}

function selectionKey(item: { kind: Kind; id: string }) {
  return `${item.kind}:${item.id}`;
}

export function TrashView() {
  const { activeWorkspace } = useWorkspace();
  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {tr.common.noWorkspace}
      </div>
    );
  }
  // Keyed by workspace: a switch drops the selection and the purge notice.
  return <Trash key={activeWorkspace.id} workspaceId={activeWorkspace.id} />;
}

/**
 * "Çöp kutusu" (ADR-009, Phase 4 branch 5): the active workspace's trashed
 * tasks and documents. Restoring is reversible and immediate. "Kalıcı
 * olarak sil" never deletes anything itself: it creates a gate-class
 * `purge_trash` approval request, which only the owner's separate decision
 * on the approvals page runs. Rows otherwise purge on their own once the
 * retention window (TRASH_RETENTION_DAYS) ends.
 */
function Trash({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const retentionDays = useWorkspace().trashRetentionDays ?? TRASH_RETENTION_DAYS_DEFAULT;
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [purgeRequest, setPurgeRequest] = useState<ApiApprovalRequest | null>(null);

  const tasks = useQuery({
    queryKey: queryKeys.trashTasks(workspaceId),
    queryFn: () =>
      apiGetJson<ApiTask[]>(`/api/tasks/trash?workspace=${encodeURIComponent(workspaceId)}`),
  });
  const documents = useQuery({
    queryKey: queryKeys.trashDocuments(workspaceId),
    queryFn: () =>
      apiGetJson<ApiKnowledgeDocument[]>(
        `/api/knowledge/documents/trash?workspace=${encodeURIComponent(workspaceId)}`,
      ),
  });

  const taskItems: TrashItem[] = (tasks.data ?? []).map((task) => ({
    kind: "task",
    id: task.id,
    title: task.title,
    deletedAt: task.deleted_at ?? task.updated_at,
  }));
  const documentItems: TrashItem[] = (documents.data ?? []).map((document) => ({
    kind: "document",
    id: document.id,
    title: document.title,
    deletedAt: document.deleted_at ?? document.updated_at,
  }));
  const allItems = [...taskItems, ...documentItems];
  // Only rows still in the trash count (a restored/purged row drops out).
  const selectedItems = allItems.filter((item) => selected.has(selectionKey(item)));

  function toggle(item: TrashItem) {
    setSelected((prev) => {
      const next = new Set(prev);
      const key = selectionKey(item);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function removeFromTrash(item: TrashItem) {
    if (item.kind === "task") {
      queryClient.setQueryData<ApiTask[]>(queryKeys.trashTasks(workspaceId), (prev = []) =>
        prev.filter((task) => task.id !== item.id),
      );
    } else {
      queryClient.setQueryData<ApiKnowledgeDocument[]>(
        queryKeys.trashDocuments(workspaceId),
        (prev = []) => prev.filter((document) => document.id !== item.id),
      );
    }
    setSelected((prev) => {
      const next = new Set(prev);
      next.delete(selectionKey(item));
      return next;
    });
  }

  async function requestPurge() {
    const request = await apiPostJson<ApiApprovalRequest>("/api/approvals/requests", {
      workspace: workspaceId,
      tool_name: "purge_trash",
      payload: {
        task_ids: selectedItems.filter((item) => item.kind === "task").map((item) => item.id),
        document_ids: selectedItems
          .filter((item) => item.kind === "document")
          .map((item) => item.id),
      },
    });
    setPurgeRequest(request);
    setSelected(new Set());
    void queryClient.invalidateQueries({ queryKey: queryKeys.allApprovals() });
    void queryClient.invalidateQueries({ queryKey: queryKeys.activitySummary() });
  }

  const loading = tasks.isPending || documents.isPending;
  const failed = (tasks.isError && !tasks.data) || (documents.isError && !documents.data);
  const tooMany = selectedItems.length > PURGE_MAX_ITEMS;

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <PageHeader title={copy.title} description={copy.description(retentionDays)} />

      {purgeRequest ? (
        <Alert role="status">
          <AlertDescription className="flex flex-wrap items-center gap-2">
            <span>{copy.purgeRequested}</span>
            <Link href={approvalHref(purgeRequest.id)} className="underline underline-offset-4">
              {copy.openRequest}
            </Link>
          </AlertDescription>
        </Alert>
      ) : null}

      {failed ? (
        <ErrorState
          error={tasks.error ?? documents.error}
          title={copy.loadFailed}
          onRetry={() => {
            void tasks.refetch();
            void documents.refetch();
          }}
          retrying={tasks.isFetching || documents.isFetching}
        />
      ) : loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : allItems.length === 0 ? (
        <EmptyState icon={Trash2} title={copy.empty} description={copy.emptyHint} />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setSelected(
                  selectedItems.length === allItems.length
                    ? new Set()
                    : new Set(allItems.map(selectionKey)),
                )
              }
            >
              {selectedItems.length === allItems.length ? copy.clearSelection : copy.selectAll}
            </Button>
            <span className="text-sm text-muted-foreground">
              {copy.selected(selectedItems.length)}
            </span>
            <ConfirmDialog
              trigger={
                <Button
                  variant="destructive"
                  size="sm"
                  disabled={selectedItems.length === 0 || tooMany}
                >
                  {copy.purge}
                </Button>
              }
              title={copy.purgeTitle}
              description={copy.purgeDescription(selectedItems.length)}
              confirmLabel={copy.purgeConfirm}
              busyLabel={copy.purging}
              errorFallback={copy.purgeFailed}
              onConfirm={requestPurge}
            >
              <ul className="max-h-40 list-inside list-disc overflow-auto text-sm">
                {selectedItems.map((item) => (
                  <li key={selectionKey(item)} className="break-words">
                    {item.title}
                  </li>
                ))}
              </ul>
            </ConfirmDialog>
            {tooMany ? (
              <span className="text-sm text-destructive">{copy.purgeLimit(PURGE_MAX_ITEMS)}</span>
            ) : null}
          </div>

          <TrashSection
            title={copy.tasks}
            empty={copy.noTasks}
            items={taskItems}
            retentionDays={retentionDays}
            selected={selected}
            onToggle={toggle}
            onRestored={removeFromTrash}
          />
          <TrashSection
            title={copy.documents}
            empty={copy.noDocuments}
            items={documentItems}
            retentionDays={retentionDays}
            selected={selected}
            onToggle={toggle}
            onRestored={removeFromTrash}
          />
        </>
      )}
    </div>
  );
}

function TrashSection({
  title,
  empty,
  items,
  retentionDays,
  selected,
  onToggle,
  onRestored,
}: {
  title: string;
  empty: string;
  items: TrashItem[];
  retentionDays: number;
  selected: Set<string>;
  onToggle: (item: TrashItem) => void;
  onRestored: (item: TrashItem) => void;
}) {
  const headingId = `trash-${title}`;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2">
      <h2 id={headingId} className="text-lg font-medium tracking-tight">
        {title} {items.length > 0 ? `(${items.length})` : ""}
      </h2>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <TrashRow
              key={selectionKey(item)}
              item={item}
              retentionDays={retentionDays}
              checked={selected.has(selectionKey(item))}
              onToggle={() => onToggle(item)}
              onRestored={() => onRestored(item)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function TrashRow({
  item,
  retentionDays,
  checked,
  onToggle,
  onRestored,
}: {
  item: TrashItem;
  retentionDays: number;
  checked: boolean;
  onToggle: () => void;
  onRestored: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Restoring is reversible (it can be trashed again), so it needs no
  // confirmation.
  async function restore() {
    setBusy(true);
    setError(null);
    try {
      await apiPostJson(
        item.kind === "task"
          ? `/api/tasks/${item.id}/restore`
          : `/api/knowledge/documents/${item.id}/restore`,
      );
      onRestored();
    } catch (err) {
      setError(errorMessage(err, copy.restoreFailed));
      setBusy(false);
    }
  }

  const purgesAt = purgeDate(item.deletedAt, retentionDays);
  return (
    <li
      data-testid={`trash-${item.kind}-${item.id}`}
      className="flex flex-col gap-2 rounded-lg border p-3"
    >
      <div className="flex flex-wrap items-start gap-3">
        <input
          type="checkbox"
          className="mt-1 size-4 accent-primary"
          aria-label={copy.select(item.title)}
          checked={checked}
          onChange={onToggle}
        />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="font-medium break-words">{item.title}</span>
          <span className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
            <span title={formatDateTime(item.deletedAt)}>
              {copy.deleted(formatRelativeTime(item.deletedAt))}
            </span>
            <span>{copy.purgesAt(formatDateTime(purgesAt))}</span>
          </span>
        </div>
        <Button variant="outline" size="sm" onClick={() => void restore()} disabled={busy}>
          {busy ? copy.restoring : copy.restore}
        </Button>
      </div>
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
    </li>
  );
}
