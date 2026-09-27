"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
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
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { fastestPollInterval, pollWhile } from "@/lib/query/polling";
import type { ApiApprovalRequest, ApiWorkspaceSummary, ApprovalStatus } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

const DECIDED_STATUSES: ApprovalStatus[] = ["approved", "rejected", "expired"];

export function ApprovalView() {
  const { activeWorkspace } = useWorkspace();

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {tr.common.noWorkspace}
      </div>
    );
  }
  return <ApprovalList key={activeWorkspace.id} workspace={activeWorkspace} />;
}

function ApprovalList({ workspace }: { workspace: ApiWorkspaceSummary }) {
  const queryClient = useQueryClient();
  // `/approvals?request=<id>` (e.g. from a harness job's gate chip):
  // scroll to and highlight that one card.
  const highlightId = useSearchParams().get("request");

  const [statusFilter, setStatusFilter] = useState<ApprovalStatus | "all">("all");
  const scrolledToRef = useRef<string | null>(null);

  // Dispatch is async (brief §2): right after an approval, `execution.status`
  // is "pending", and only the worker's later dispatch moves it on. Poll
  // only while something is actually in flight; stops on its own once every
  // execution is terminal. Harness executions track the implementation
  // job's own status: fast while a container is working, slow while a gate
  // just waits for the owner. Never while the tab is hidden.
  const approvalsKey = queryKeys.approvals(workspace.id);
  const query = useQuery({
    queryKey: approvalsKey,
    queryFn: () =>
      apiGetJson<ApiApprovalRequest[]>(
        `/api/approvals/requests?workspace=${encodeURIComponent(workspace.id)}`,
      ),
    refetchInterval: pollWhile<ApiApprovalRequest[]>((list) =>
      fastestPollInterval(list.map(executionPollInterval)),
    ),
  });
  const requests = useMemo(() => query.data ?? [], [query.data]);
  const loading = query.isPending;

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
    queryClient.setQueryData<ApiApprovalRequest[]>(approvalsKey, (prev = []) =>
      prev.map((r) => (r.id === updated.id ? updated : r)),
    );
    // The nav badge counts pending requests.
    void queryClient.invalidateQueries({ queryKey: queryKeys.activitySummary() });
  }

  const pending = requests.filter((r) => r.status === "pending");
  const decided = requests
    .filter((r) => r.status !== "pending")
    .filter((r) => statusFilter === "all" || r.status === statusFilter || r.id === highlightId);
  // Only owner/admin roles may decide (brief §4, approvals.services
  // _CURATOR_ROLES) -- members/viewers would otherwise see Approve/Reject
  // buttons that always fail with a 403 (Codex review, branch 4).
  const canDecide = workspace.role === "owner" || workspace.role === "admin";
  const highlightMissing =
    highlightId != null && query.isSuccess && !requests.some((r) => r.id === highlightId);

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <PageHeader
        title="Onaylar"
        description="İnsan kararı gerektiren yazma/silme/dağıtım/dışa-gönderme istekleri."
      />

      {query.isError && !query.data ? (
        <ErrorState
          error={query.error}
          title="Onay istekleri yüklenemedi."
          onRetry={() => void query.refetch()}
          retrying={query.isFetching}
        />
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
