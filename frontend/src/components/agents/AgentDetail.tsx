"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { AgentStepRow } from "@/components/agents/AgentStepRow";
import {
  AGENT_IN_FLIGHT_STATUSES,
  AGENT_STATUS_LABELS,
  agentFailureReasonLabel,
} from "@/components/agents/labels";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson, apiPostJson, errorMessage } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { POLL_FAST_MS, pollWhile } from "@/lib/query/polling";
import type { ApiAgentDetail } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

/**
 * Detail view for one `Agent` (route `/agents/[agentId]`): its ordered
 * `AgentStep` list, each step's own status -- the direct rendering of the
 * visibility brake. Polls while the Agent is still in flight
 * (planning/running/blocked_on_*) and stops on its own once it reaches a
 * terminal status.
 */
export function AgentDetail({ agentId }: { agentId: string }) {
  const { activeWorkspace } = useWorkspace();
  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {tr.common.noWorkspace}
      </div>
    );
  }
  return <AgentDetailBody key={activeWorkspace.id} workspaceId={activeWorkspace.id} agentId={agentId} />;
}

function AgentDetailBody({ workspaceId, agentId }: { workspaceId: string; agentId: string }) {
  const queryClient = useQueryClient();
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const detailKey = queryKeys.agent(workspaceId, agentId);
  const query = useQuery({
    queryKey: detailKey,
    queryFn: () => apiGetJson<ApiAgentDetail>(`/api/agents/${agentId}`),
    refetchInterval: pollWhile<ApiAgentDetail>((agent) =>
      AGENT_IN_FLIGHT_STATUSES.includes(agent.status) ? POLL_FAST_MS : null,
    ),
  });
  const agent = query.data;

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: detailKey });
    // The list shows this agent's status too.
    void queryClient.invalidateQueries({ queryKey: queryKeys.agents(workspaceId), exact: true });
  }

  async function handleCancel() {
    setCancelling(true);
    setCancelError(null);
    try {
      await apiPostJson(`/api/agents/${agentId}/cancel`);
      refresh();
    } catch (err) {
      setCancelError(errorMessage(err, "Ajan iptal edilemedi."));
    } finally {
      setCancelling(false);
    }
  }

  const canCancel =
    agent != null &&
    agent.status !== "completed" &&
    agent.status !== "failed" &&
    agent.status !== "cancelled";

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-6")}>
      <Button variant="outline" size="sm" className="self-start" asChild>
        <Link href="/agents">&larr; Ajanlara dön</Link>
      </Button>

      {!agent ? (
        query.isPending ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <ErrorState
            error={query.error}
            title="Bu ajan yüklenemedi."
            onRetry={() => void query.refetch()}
            retrying={query.isFetching}
          />
        )
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex min-w-0 flex-col gap-1.5">
              <h1 className="text-xl font-semibold tracking-tight">{agent.goal}</h1>
              <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <StatusPill
                  domain="agent"
                  status={agent.status}
                  label={AGENT_STATUS_LABELS[agent.status] ?? agent.status}
                />
                {agent.failure_reason ? (
                  <span>{agentFailureReasonLabel(agent.failure_reason)}</span>
                ) : null}
              </p>
              {agent.skill_name ? (
                <p className="text-sm text-muted-foreground">Şu beceriyle çalıştı: {agent.skill_name}</p>
              ) : null}
            </div>
            {canCancel ? (
              <Button variant="outline" onClick={() => void handleCancel()} disabled={cancelling}>
                {cancelling ? "İptal ediliyor…" : "Ajanı iptal et"}
              </Button>
            ) : null}
          </div>

          {cancelError ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{cancelError}</AlertDescription>
            </Alert>
          ) : null}

          <ul className="flex flex-col gap-2">
            {agent.steps.map((step) => (
              <AgentStepRow key={step.id} step={step} onResolved={refresh} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
