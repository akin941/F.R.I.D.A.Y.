"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { AgentGoalForm } from "@/components/agents/AgentGoalForm";
import {
  AGENT_IN_FLIGHT_STATUSES,
  AGENT_STATUS_LABELS,
  agentFailureReasonLabel,
} from "@/components/agents/labels";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { POLL_FAST_MS, pollWhile } from "@/lib/query/polling";
import type { ApiAgent } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

export function agentDetailHref(agent: Pick<ApiAgent, "id" | "workspace">): string {
  return `/agents/${agent.id}?workspace=${encodeURIComponent(agent.workspace)}`;
}

export function AgentsView() {
  const { activeWorkspace } = useWorkspace();

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {tr.common.noWorkspace}
      </div>
    );
  }
  // Keyed by workspace: switching workspace resets local UI state (the
  // open create form); the query key isolates the rows themselves.
  return <AgentList key={activeWorkspace.id} workspaceId={activeWorkspace.id} />;
}

function AgentList({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);

  // Keep in-flight rows' statuses fresh while the list is open; stops on
  // its own once every agent is terminal.
  const agents = useQuery({
    queryKey: queryKeys.agents(workspaceId),
    queryFn: () =>
      apiGetJson<ApiAgent[]>(`/api/agents?workspace=${encodeURIComponent(workspaceId)}`),
    refetchInterval: pollWhile<ApiAgent[]>((list) =>
      list.some((a) => AGENT_IN_FLIGHT_STATUSES.includes(a.status)) ? POLL_FAST_MS : null,
    ),
  });

  function handleCreated(agent: ApiAgent) {
    queryClient.setQueryData<ApiAgent[]>(queryKeys.agents(agent.workspace), (prev = []) => [
      agent,
      ...prev.filter((a) => a.id !== agent.id),
    ]);
    setCreating(false);
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <PageHeader
        title="Ajanlar"
        description="Hedefi ver, çok adımlı planı al — adım adım onay yok, ama her yazma/silme/dağıtım/dışa-gönderme işlemi yine Onaylar üzerinden geçer."
        actions={
          <Button onClick={() => setCreating((v) => !v)}>{creating ? "İptal" : "Yeni ajan"}</Button>
        }
      />

      {agents.isError && !agents.data ? (
        <ErrorState
          error={agents.error}
          title="Ajanlar yüklenemedi."
          onRetry={() => void agents.refetch()}
          retrying={agents.isFetching}
        />
      ) : null}

      {creating ? (
        <AgentGoalForm
          workspaceId={workspaceId}
          onCreated={handleCreated}
          onCancel={() => setCreating(false)}
        />
      ) : null}

      {agents.isPending ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : agents.data?.length === 0 ? (
        <EmptyState icon={Bot} title="Henüz ajan yok." />
      ) : agents.data ? (
        <ul className="flex flex-col gap-3">
          {agents.data.map((agent) => (
            <li key={agent.id}>
              <Link
                href={agentDetailHref(agent)}
                className="block rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <Card className="transition-colors hover:bg-accent/50">
                  <CardHeader>
                    <CardTitle className="text-base">{agent.goal}</CardTitle>
                  </CardHeader>
                  <CardContent className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                    <StatusPill
                      domain="agent"
                      status={agent.status}
                      label={AGENT_STATUS_LABELS[agent.status] ?? agent.status}
                    />
                    {agent.failure_reason ? (
                      <span>{agentFailureReasonLabel(agent.failure_reason)}</span>
                    ) : null}
                  </CardContent>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
