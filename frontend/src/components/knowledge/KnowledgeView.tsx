"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { KnowledgeCard } from "@/components/knowledge/KnowledgeCard";
import { KnowledgeCreateForm } from "@/components/knowledge/KnowledgeCreateForm";
import { KNOWLEDGE_STATUS_LABELS, KNOWLEDGE_STATUSES } from "@/components/knowledge/labels";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import type { ApiKnowledgeDocument, KnowledgeStatus } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

export function KnowledgeView() {
  const { activeWorkspace } = useWorkspace();

  const [documents, setDocuments] = useState<ApiKnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<KnowledgeStatus | "all">("all");
  const [creating, setCreating] = useState(false);

  const activeWorkspaceIdRef = useRef<string | null>(null);

  const loadDocuments = useCallback(async (workspaceId: string) => {
    setLoading(true);
    setLoadError(null);
    try {
      const list = await apiGetJson<ApiKnowledgeDocument[]>(
        `/api/knowledge/documents?workspace=${encodeURIComponent(workspaceId)}`,
      );
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setDocuments(list);
    } catch {
      if (activeWorkspaceIdRef.current === workspaceId) {
        setLoadError("Bilgi belgeleri yüklenemedi.");
      }
    } finally {
      if (activeWorkspaceIdRef.current === workspaceId) setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount/workspace-change, the standard data-fetching effect pattern */
  useEffect(() => {
    const workspaceId = activeWorkspace?.id ?? null;
    activeWorkspaceIdRef.current = workspaceId;
    setDocuments([]);
    // Same guard as MemoryView: a workspace switch must not leave a stale
    // create-form draft mounted across workspaces.
    setCreating(false);
    if (workspaceId) {
      void loadDocuments(workspaceId);
    }
  }, [activeWorkspace, loadDocuments]);
  /* eslint-enable react-hooks/set-state-in-effect */

  function handleDeleted(id: string) {
    setDocuments((prev) => prev.filter((d) => d.id !== id));
  }

  function handleCreated(document: ApiKnowledgeDocument) {
    // Discard a creation response that resolves after the user has already
    // switched to a different workspace.
    if (activeWorkspaceIdRef.current !== document.workspace) return;
    setDocuments((prev) => [document, ...prev]);
    setCreating(false);
  }

  const visible = documents.filter(
    (d) => statusFilter === "all" || d.status === statusFilter,
  );

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        Kullanılabilir çalışma alanı yok.
      </div>
    );
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Bilgi</h1>
          <p className="text-sm text-muted-foreground">
            Jarvis&apos;in sohbet bağlamına getirebileceği belgeler.
          </p>
        </div>
        <Button onClick={() => setCreating((v) => !v)}>
          {creating ? "İptal" : "Yeni belge"}
        </Button>
      </div>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {creating ? (
        // Keyed by workspace id, same rationale as MemoryCreateForm above.
        <KnowledgeCreateForm
          key={activeWorkspace.id}
          workspaceId={activeWorkspace.id}
          onCreated={handleCreated}
          onCancel={() => setCreating(false)}
        />
      ) : null}

      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <section className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-medium tracking-tight">Belgeler</h2>
            <Select
              value={statusFilter}
              onValueChange={(v) => setStatusFilter(v as KnowledgeStatus | "all")}
            >
              <SelectTrigger aria-label="Durum filtresi">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Tüm durumlar</SelectItem>
                {KNOWLEDGE_STATUSES.map((status) => (
                  <SelectItem key={status} value={status}>
                    {KNOWLEDGE_STATUS_LABELS[status]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {visible.length === 0 ? (
            <p className="text-sm text-muted-foreground">Bu filtrelere uyan belge yok.</p>
          ) : (
            <div className="flex flex-col gap-3">
              {visible.map((document) => (
                <KnowledgeCard key={document.id} document={document} onDeleted={handleDeleted} />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
