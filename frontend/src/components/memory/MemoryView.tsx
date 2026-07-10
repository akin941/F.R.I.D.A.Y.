"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { MemoryCard } from "@/components/memory/MemoryCard";
import { MemoryCreateForm } from "@/components/memory/MemoryCreateForm";
import { MEMORY_STATUS_LABELS, MEMORY_TYPE_LABELS, MEMORY_TYPES } from "@/components/memory/labels";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson, apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiMemory, MemoryStatus, MemoryType } from "@/types/api";

const LIST_STATUSES: MemoryStatus[] = ["approved", "rejected", "archived"];

export function MemoryView() {
  const { activeWorkspace } = useWorkspace();

  const [memories, setMemories] = useState<ApiMemory[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<MemoryStatus | "all">("all");
  const [typeFilter, setTypeFilter] = useState<MemoryType | "all">("all");
  const [creating, setCreating] = useState(false);
  const [vaultExportNotice, setVaultExportNotice] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);
  const [vaultExportBusy, setVaultExportBusy] = useState(false);

  const activeWorkspaceIdRef = useRef<string | null>(null);

  const loadMemories = useCallback(async (workspaceId: string) => {
    setLoading(true);
    setLoadError(null);
    try {
      const list = await apiGetJson<ApiMemory[]>(
        `/api/memories?workspace=${encodeURIComponent(workspaceId)}`,
      );
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setMemories(list);
    } catch {
      if (activeWorkspaceIdRef.current === workspaceId) {
        setLoadError("Failed to load memories.");
      }
    } finally {
      if (activeWorkspaceIdRef.current === workspaceId) setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount/workspace-change, the standard data-fetching effect pattern */
  useEffect(() => {
    const workspaceId = activeWorkspace?.id ?? null;
    activeWorkspaceIdRef.current = workspaceId;
    setMemories([]);
    // A workspace switch must not leave a stale create-form draft mounted:
    // MemoryCreateForm is also re-keyed by workspace id below, which resets
    // its internal fields on remount, but `creating` itself is closed here
    // too so a draft started in workspace A never lingers visibly into B.
    setCreating(false);
    // Same reasoning for a stale vault-export notice/busy-state left over
    // from the previous workspace (Codex review, branch 4).
    setVaultExportNotice(null);
    setVaultExportBusy(false);
    if (workspaceId) {
      void loadMemories(workspaceId);
    }
  }, [activeWorkspace, loadMemories]);
  /* eslint-enable react-hooks/set-state-in-effect */

  function handleChanged(updated: ApiMemory) {
    setMemories((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
  }

  function handleDeleted(id: string) {
    setMemories((prev) => prev.filter((m) => m.id !== id));
  }

  function handleCreated(memory: ApiMemory) {
    // Discard a creation response that resolves after the user has already
    // switched to a different workspace — otherwise a workspace-A memory
    // could be unconditionally prepended into workspace B's list.
    if (activeWorkspaceIdRef.current !== memory.workspace) return;
    setMemories((prev) => [memory, ...prev]);
    setCreating(false);
  }

  function handleExport() {
    if (!activeWorkspace) return;
    window.open(
      `/api/memories/export?workspace=${encodeURIComponent(activeWorkspace.id)}`,
      "_blank",
    );
  }

  async function handleVaultExportRequest() {
    if (!activeWorkspace || vaultExportBusy) return;
    const workspaceId = activeWorkspace.id;
    setVaultExportBusy(true);
    setVaultExportNotice(null);
    try {
      await apiPostJson("/api/approvals/requests", {
        workspace: workspaceId,
        tool_name: "vault_export",
      });
      // Discard this response if the user has already switched to a
      // different workspace before the POST resolved -- otherwise
      // workspace A's success/error notice could show on workspace B, and
      // B's button could stay disabled until A's request finishes (Codex
      // review, branch 4; mirrors handleCreated's own workspace guard).
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      // Because a personal workspace today has exactly one member (its
      // owner), and requester != decider is enforced without exception in
      // production, this request cannot be approved by anyone in a
      // personal workspace today (brief §4) -- say so plainly rather than
      // implying the user can just go approve it themselves.
      setVaultExportNotice({
        type: "success",
        message: "Request created. It needs approval from another workspace admin before it runs.",
      });
    } catch (err) {
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setVaultExportNotice({
        type: "error",
        message:
          err instanceof ApiError
            ? (err.body.detail ?? "Could not create the export request.")
            : "Could not create the export request.",
      });
    } finally {
      if (activeWorkspaceIdRef.current === workspaceId) setVaultExportBusy(false);
    }
  }

  const pending = memories.filter((m) => m.status === "pending_approval");
  const others = memories
    .filter((m) => m.status !== "pending_approval")
    .filter((m) => statusFilter === "all" || m.status === statusFilter)
    .filter((m) => typeFilter === "all" || m.memory_type === typeFilter);

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        No workspace available.
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Memory</h1>
          <p className="text-sm text-muted-foreground">
            Curated facts, preferences and episodes Jarvis remembers.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleExport}>
            Export
          </Button>
          <Button
            variant="outline"
            onClick={() => void handleVaultExportRequest()}
            disabled={vaultExportBusy}
          >
            Export to Obsidian vault
          </Button>
          <Button onClick={() => setCreating((v) => !v)}>
            {creating ? "Cancel" : "New memory"}
          </Button>
        </div>
      </div>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {vaultExportNotice ? (
        <Alert
          variant={vaultExportNotice.type === "error" ? "destructive" : "default"}
          role="alert"
        >
          <AlertDescription>{vaultExportNotice.message}</AlertDescription>
        </Alert>
      ) : null}

      {creating ? (
        // Keyed by workspace id: a workspace switch forces React to unmount
        // and remount this form instead of reusing it with a stale draft
        // and a swapped-out workspaceId prop (Codex finding — see the
        // handleCreated/effect comments above for the matching guards).
        <MemoryCreateForm
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
        <>
          <section className="flex flex-col gap-3">
            <h2 className="text-lg font-medium tracking-tight">
              Pending approval {pending.length > 0 ? `(${pending.length})` : ""}
            </h2>
            {pending.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing waiting for review.</p>
            ) : (
              <div className="flex flex-col gap-3">
                {pending.map((memory) => (
                  <MemoryCard
                    key={memory.id}
                    memory={memory}
                    variant="pending"
                    onChanged={handleChanged}
                    onDeleted={handleDeleted}
                  />
                ))}
              </div>
            )}
          </section>

          <Separator />

          <section className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-medium tracking-tight">All memories</h2>
              <div className="flex gap-2">
                <Select
                  value={statusFilter}
                  onValueChange={(v) => setStatusFilter(v as MemoryStatus | "all")}
                >
                  <SelectTrigger aria-label="Status filter">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All statuses</SelectItem>
                    {LIST_STATUSES.map((status) => (
                      <SelectItem key={status} value={status}>
                        {MEMORY_STATUS_LABELS[status]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={typeFilter}
                  onValueChange={(v) => setTypeFilter(v as MemoryType | "all")}
                >
                  <SelectTrigger aria-label="Type filter">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All types</SelectItem>
                    {MEMORY_TYPES.map((type) => (
                      <SelectItem key={type} value={type}>
                        {MEMORY_TYPE_LABELS[type]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            {others.length === 0 ? (
              <p className="text-sm text-muted-foreground">No memories match these filters.</p>
            ) : (
              <div className="flex flex-col gap-3">
                {others.map((memory) => (
                  <MemoryCard
                    key={memory.id}
                    memory={memory}
                    variant="list"
                    onChanged={handleChanged}
                    onDeleted={handleDeleted}
                  />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
