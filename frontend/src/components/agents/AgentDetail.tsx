"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { AgentStepRow } from "@/components/agents/AgentStepRow";
import { AGENT_IN_FLIGHT_STATUSES, AGENT_STATUS_LABELS } from "@/components/agents/labels";
import { apiGetJson, apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiAgentDetail } from "@/types/api";

/**
 * Detail view for one `Agent` (brief §5): its ordered `AgentStep` list,
 * each step's own status -- the direct rendering of §3's visibility brake.
 * Polls silently while the Agent is still in flight (planning/running/
 * blocked_on_*), mirroring `ApprovalView`'s identical run-status poll --
 * stops on its own once the Agent reaches a terminal status.
 */
export function AgentDetail({ agentId, onBack }: { agentId: string; onBack: () => void }) {
  const [agent, setAgent] = useState<ApiAgentDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const loadAgent = useCallback(
    async (options?: { silent?: boolean }) => {
      if (!options?.silent) setLoading(true);
      setLoadError(null);
      try {
        const data = await apiGetJson<ApiAgentDetail>(`/api/agents/${agentId}`);
        setAgent(data);
      } catch {
        if (!options?.silent) setLoadError("Failed to load this agent.");
      } finally {
        if (!options?.silent) setLoading(false);
      }
    },
    [agentId],
  );

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount, the standard data-fetching effect pattern */
  useEffect(() => {
    void loadAgent();
  }, [loadAgent]);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    if (!agent || !AGENT_IN_FLIGHT_STATUSES.includes(agent.status)) return;
    const interval = setInterval(() => {
      void loadAgent({ silent: true });
    }, 3000);
    return () => clearInterval(interval);
  }, [agent, loadAgent]);

  async function handleCancel() {
    setCancelling(true);
    setCancelError(null);
    try {
      await apiPostJson(`/api/agents/${agentId}/cancel`);
      await loadAgent({ silent: true });
    } catch (err) {
      setCancelError(
        err instanceof ApiError
          ? (err.body.detail ?? "That agent could not be cancelled.")
          : "Something went wrong. Please try again.",
      );
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
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-6">
      <Button variant="outline" size="sm" className="self-start" onClick={onBack}>
        &larr; Back to agents
      </Button>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {loading || !agent ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-xl font-semibold tracking-tight">{agent.goal}</h1>
              <p className="text-sm text-muted-foreground">
                {AGENT_STATUS_LABELS[agent.status]}
                {agent.failure_reason ? ` — ${agent.failure_reason}` : ""}
              </p>
              {agent.skill_name ? (
                <p className="text-sm text-muted-foreground">Ran via skill: {agent.skill_name}</p>
              ) : null}
            </div>
            {canCancel ? (
              <Button
                variant="outline"
                onClick={() => void handleCancel()}
                disabled={cancelling}
              >
                {cancelling ? "Cancelling…" : "Cancel agent"}
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
              <AgentStepRow key={step.id} step={step} onResolved={() => void loadAgent()} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
