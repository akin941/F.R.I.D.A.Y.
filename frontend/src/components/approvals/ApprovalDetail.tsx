"use client";

import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { canDecideInRole } from "@/components/approvals/labels";
import { requestPollInterval } from "@/components/approvals/polling";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { pollWhile } from "@/lib/query/polling";
import type { ApiApprovalRequest } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

const copy = tr.approvals;

/**
 * `/approvals/[requestId]` (Phase 4 branch 5): one request, deep-linkable
 * from a harness gate chip, a chat tool request or the inbox -- whichever
 * of the owner's workspaces it belongs to (the backend 404s anything
 * outside their memberships). The same card as the inbox, with the
 * decision dialogs for a curator; polls like the inbox while it can still
 * change.
 */
export function ApprovalDetail({ requestId }: { requestId: string }) {
  const queryClient = useQueryClient();
  const { workspaces } = useWorkspace();
  const detailKey = queryKeys.approvalDetail(requestId);

  const query = useQuery({
    queryKey: detailKey,
    queryFn: () =>
      apiGetJson<ApiApprovalRequest>(`/api/approvals/requests/${encodeURIComponent(requestId)}`),
    refetchInterval: pollWhile<ApiApprovalRequest>(requestPollInterval),
  });
  const request = query.data ?? null;
  const workspace = request
    ? (workspaces ?? []).find((candidate) => candidate.id === request.workspace)
    : undefined;

  function handleChanged(updated: ApiApprovalRequest) {
    queryClient.setQueryData(detailKey, updated);
    void queryClient.invalidateQueries({ queryKey: queryKeys.allApprovals() });
    void queryClient.invalidateQueries({ queryKey: queryKeys.activitySummary() });
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-6")}>
      <Button variant="outline" size="sm" className="self-start" asChild>
        <Link href="/approvals">&larr; {copy.detail.back}</Link>
      </Button>

      {!request ? (
        query.isPending ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : (
          <ErrorState
            error={query.error}
            title={copy.detail.loadFailed}
            onRetry={() => void query.refetch()}
            retrying={query.isFetching}
          />
        )
      ) : (
        <ApprovalCard
          request={request}
          workspaceName={workspace?.name ?? copy.inbox.unknownWorkspace}
          canDecide={canDecideInRole(workspace?.role)}
          onChanged={handleChanged}
          linkToDetail={false}
          headingLevel="h1"
        />
      )}
    </div>
  );
}
