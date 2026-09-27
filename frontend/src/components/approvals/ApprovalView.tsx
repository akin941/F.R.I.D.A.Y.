"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { APPROVAL_STATUS_LABELS, executionPollInterval } from "@/components/approvals/labels";
import { fastestPollInterval } from "@/components/harness/labels";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import { usePolling } from "@/lib/use-polling";
import type { ApiApprovalRequest, ApprovalStatus } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

const DECIDED_STATUSES: ApprovalStatus[] = ["approved", "rejected", "expired"];

export function ApprovalView() {
  const { activeWorkspace } = useWorkspace();
  // `/approvals?request=<id>` (e.g. from a harness job's gate chip):
  // scroll to and highlight that one card.
  const highlightId = useSearchParams().get("request");

  const [requests, setRequests] = useState<ApiApprovalRequest[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<ApprovalStatus | "all">("all");
  const [loadedWorkspaceId, setLoadedWorkspaceId] = useState<string | null>(null);

  const activeWorkspaceIdRef = useRef<string | null>(null);
  const scrolledToRef = useRef<string | null>(null);

  const loadRequests = useCallback(async (workspaceId: string, options?: { silent?: boolean }) => {
    if (!options?.silent) setLoading(true);
    setLoadError(null);
    try {
      const list = await apiGetJson<ApiApprovalRequest[]>(
        `/api/approvals/requests?workspace=${encodeURIComponent(workspaceId)}`,
      );
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setRequests(list);
      setLoadedWorkspaceId(workspaceId);
    } catch {
      if (activeWorkspaceIdRef.current === workspaceId && !options?.silent) {
        setLoadError("Onay istekleri yüklenemedi.");
      }
    } finally {
      if (activeWorkspaceIdRef.current === workspaceId && !options?.silent) setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount/workspace-change, the standard data-fetching effect pattern */
  useEffect(() => {
    const workspaceId = activeWorkspace?.id ?? null;
    activeWorkspaceIdRef.current = workspaceId;
    setRequests([]);
    if (workspaceId) {
      void loadRequests(workspaceId);
    }
  }, [activeWorkspace, loadRequests]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Dispatch is async (brief §2): right after an approval, `execution.status`
  // is "pending" (create_execution already ran in the same transaction), and
  // only the worker's later dispatch moves it to "running"/"succeeded"/
  // "failed". Without this, a user who stays on the page sees the run-status
  // indicator stuck at "Waiting to run" until a manual reload (Codex review,
  // branch 4). Poll silently -- no skeleton flash -- only while something is
  // actually in flight; stops on its own once every execution is terminal.
  // Harness executions track the implementation job's own status: fast
  // while a container is working, slow (not every 3s for hours) while a
  // gate just waits for the owner. Paused while the tab is hidden.
  const pollInterval = activeWorkspace
    ? fastestPollInterval(requests.map(executionPollInterval))
    : null;
  usePolling(() => {
    const workspaceId = activeWorkspaceIdRef.current;
    if (workspaceId) void loadRequests(workspaceId, { silent: true });
  }, pollInterval);

  // Scroll the requested card into view once, the first time it renders.
  useEffect(() => {
    if (!highlightId || scrolledToRef.current === highlightId) return;
    if (!requests.some((r) => r.id === highlightId)) return;
    const element = document.getElementById(`approval-${highlightId}`);
    if (!element) return;
    scrolledToRef.current = highlightId;
    if (typeof element.scrollIntoView === "function") {
      element.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    element.focus({ preventScroll: true });
  }, [highlightId, requests, loading]);

  function handleChanged(updated: ApiApprovalRequest) {
    setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
  }

  const pending = requests.filter((r) => r.status === "pending");
  const decided = requests
    .filter((r) => r.status !== "pending")
    .filter((r) => statusFilter === "all" || r.status === statusFilter || r.id === highlightId);
  // Only owner/admin roles may decide (brief §4, approvals.services
  // _CURATOR_ROLES) -- members/viewers would otherwise see Approve/Reject
  // buttons that always fail with a 403 (Codex review, branch 4).
  const canDecide = activeWorkspace?.role === "owner" || activeWorkspace?.role === "admin";
  const highlightMissing =
    highlightId != null &&
    !loading &&
    loadedWorkspaceId === activeWorkspace?.id &&
    !requests.some((r) => r.id === highlightId);

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        Kullanılabilir çalışma alanı yok.
      </div>
    );
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Onaylar</h1>
        <p className="text-sm text-muted-foreground">
          İnsan kararı gerektiren yazma/silme/dağıtım/dışa-gönderme istekleri.
        </p>
      </div>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {highlightMissing ? (
        <Alert>
          <AlertDescription>
            İstenen onay isteği bu çalışma alanında bulunamadı — başka bir çalışma alanına ait
            olabilir.
          </AlertDescription>
        </Alert>
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
              Bekleyenler {pending.length > 0 ? `(${pending.length})` : ""}
            </h2>
            {pending.length === 0 ? (
              <p className="text-sm text-muted-foreground">Karar bekleyen bir şey yok.</p>
            ) : (
              <div className="flex flex-col gap-3">
                {pending.map((request) => (
                  <ApprovalCard
                    key={request.id}
                    request={request}
                    canDecide={canDecide}
                    onChanged={handleChanged}
                    highlighted={request.id === highlightId}
                  />
                ))}
              </div>
            )}
          </section>

          <Separator />

          <section className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-medium tracking-tight">Karara bağlananlar</h2>
              <Select
                value={statusFilter}
                onValueChange={(v) => setStatusFilter(v as ApprovalStatus | "all")}
              >
                <SelectTrigger aria-label="Durum filtresi">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Tüm durumlar</SelectItem>
                  {DECIDED_STATUSES.map((status) => (
                    <SelectItem key={status} value={status}>
                      {APPROVAL_STATUS_LABELS[status]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {decided.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Bu filtreye uyan karara bağlanmış istek yok.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                {decided.map((request) => (
                  <ApprovalCard
                    key={request.id}
                    request={request}
                    canDecide={canDecide}
                    onChanged={handleChanged}
                    highlighted={request.id === highlightId}
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
