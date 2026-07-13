"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { AgentGoalForm } from "@/components/agents/AgentGoalForm";
import { AgentDetail } from "@/components/agents/AgentDetail";
import { AGENT_IN_FLIGHT_STATUSES, AGENT_STATUS_LABELS } from "@/components/agents/labels";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import type { ApiAgent } from "@/types/api";

export function AgentsView() {
  const { activeWorkspace } = useWorkspace();

  const [agents, setAgents] = useState<ApiAgent[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

  const activeWorkspaceIdRef = useRef<string | null>(null);

  const loadAgents = useCallback(async (workspaceId: string, options?: { silent?: boolean }) => {
    if (!options?.silent) setLoading(true);
    setLoadError(null);
    try {
      const list = await apiGetJson<ApiAgent[]>(
        `/api/agents?workspace=${encodeURIComponent(workspaceId)}`,
      );
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setAgents(list);
    } catch {
      if (activeWorkspaceIdRef.current === workspaceId && !options?.silent) {
        setLoadError("Failed to load agents.");
      }
    } finally {
      if (activeWorkspaceIdRef.current === workspaceId && !options?.silent) setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount/workspace-change, the standard data-fetching effect pattern */
  useEffect(() => {
    const workspaceId = activeWorkspace?.id ?? null;
    activeWorkspaceIdRef.current = workspaceId;
    setAgents([]);
    setCreating(false);
    setSelectedAgentId(null);
    if (workspaceId) {
      void loadAgents(workspaceId);
    }
  }, [activeWorkspace, loadAgents]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // While viewing the list (not a single agent's own detail poll), keep
  // in-flight rows' statuses fresh too -- same reasoning as ApprovalView.
  useEffect(() => {
    const workspaceId = activeWorkspace?.id ?? null;
    if (!workspaceId || selectedAgentId) return;
    const hasInFlight = agents.some((a) => AGENT_IN_FLIGHT_STATUSES.includes(a.status));
    if (!hasInFlight) return;
    const interval = setInterval(() => {
      void loadAgents(workspaceId, { silent: true });
    }, 3000);
    return () => clearInterval(interval);
  }, [agents, selectedAgentId, activeWorkspace, loadAgents]);

  function handleCreated(agent: ApiAgent) {
    if (activeWorkspaceIdRef.current !== agent.workspace) return;
    setAgents((prev) => [agent, ...prev]);
    setCreating(false);
  }

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        No workspace available.
      </div>
    );
  }

  if (selectedAgentId) {
    return (
      <AgentDetail agentId={selectedAgentId} onBack={() => setSelectedAgentId(null)} />
    );
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
          <p className="text-sm text-muted-foreground">
            Goal in, multi-step plan out — no per-step confirmation, but every
            write/delete/deploy/external-send action still goes through Approvals.
          </p>
        </div>
        <Button onClick={() => setCreating((v) => !v)}>{creating ? "Cancel" : "New agent"}</Button>
      </div>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {creating ? (
        <AgentGoalForm
          key={activeWorkspace.id}
          workspaceId={activeWorkspace.id}
          onCreated={handleCreated}
          onCancel={() => setCreating(false)}
        />
      ) : null}

      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : agents.length === 0 ? (
        <p className="text-sm text-muted-foreground">No agents yet.</p>
      ) : (
        <div className="flex flex-col gap-3">
          {agents.map((agent) => (
            <Card
              key={agent.id}
              className="cursor-pointer transition-colors hover:bg-accent/50"
              onClick={() => setSelectedAgentId(agent.id)}
            >
              <CardHeader>
                <CardTitle className="text-base">{agent.goal}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">
                  {AGENT_STATUS_LABELS[agent.status]}
                  {agent.failure_reason ? ` — ${agent.failure_reason}` : ""}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
