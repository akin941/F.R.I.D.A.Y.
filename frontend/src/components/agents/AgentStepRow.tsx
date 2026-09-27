"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AGENT_STEP_KIND_LABELS, AGENT_STEP_STATUS_LABELS } from "@/components/agents/labels";
import { apiPostJson, errorMessage } from "@/lib/api-client";
import type { ApiAgentStep } from "@/types/api";

function readResultSummary(step: ApiAgentStep): string | null {
  const summary = step.result?.summary;
  return typeof summary === "string" ? summary : null;
}

/**
 * One `AgentStep` row on the agent detail page (brief §5). A
 * `blocked_on_approval` step links to `/approvals` (the engine already
 * created its `ApprovalRequest` -- the existing page is where a curator
 * decides it, the same "confirm here, decide there" split MVP 6's
 * `tool_request` chat card already established). A `blocked_on_confirmation`
 * `task_draft` step renders its own inline confirm/discard control, calling
 * the agents-local endpoint (never the existing tasks API's
 * `draft_message_id` mechanism -- brief §3's round-4 fix).
 */
export function AgentStepRow({
  step,
  onResolved,
}: {
  step: ApiAgentStep;
  // Confirming returns the created Task (not the step), and either action
  // can also move the parent Agent's own status (e.g. to `completed`) --
  // the detail view re-fetches the whole agent on any resolution rather
  // than this row trying to patch its own local state.
  onResolved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm() {
    setBusy(true);
    setError(null);
    try {
      await apiPostJson(`/api/agents/steps/${step.id}/confirm`);
      onResolved();
    } catch (err) {
      setError(errorMessage(err, "Görev oluşturulamadı."));
      setBusy(false);
    }
  }

  async function handleDiscard() {
    setBusy(true);
    setError(null);
    try {
      await apiPostJson(`/api/agents/steps/${step.id}/discard`);
      onResolved();
    } catch (err) {
      setError(errorMessage(err, "Adım atılamadı."));
      setBusy(false);
    }
  }

  const summary = readResultSummary(step);

  return (
    <li className="flex flex-col gap-2 rounded-md border p-3">
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium">
          {step.description || AGENT_STEP_KIND_LABELS[step.kind]}
        </span>
        <span className="text-xs text-muted-foreground">
          {AGENT_STEP_KIND_LABELS[step.kind]} &middot; {AGENT_STEP_STATUS_LABELS[step.status]}
        </span>
      </div>
      {summary ? <p className="text-sm text-muted-foreground">{summary}</p> : null}
      {step.status === "blocked_on_approval" ? (
        <Link href="/approvals" className="text-sm underline">Onaylar sayfasında incele</Link>
      ) : null}
      {step.status === "blocked_on_confirmation" && step.kind === "task_draft" ? (
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => void handleDiscard()} disabled={busy}>Vazgeç</Button>
          <Button size="sm" onClick={() => void handleConfirm()} disabled={busy}>
            {busy ? "İşleniyor…" : "Görevi onayla"}
          </Button>
        </div>
      ) : null}
    </li>
  );
}
