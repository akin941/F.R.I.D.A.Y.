"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { APPROVAL_STATUS_LABELS, EXECUTION_STATUS_LABELS } from "@/components/approvals/labels";
import { apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiApprovalRequest } from "@/types/api";

export function ApprovalCard({
  request,
  currentUserId,
  canDecide,
  onChanged,
}: {
  request: ApiApprovalRequest;
  currentUserId: string | null;
  canDecide: boolean;
  onChanged: (request: ApiApprovalRequest) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // requester != decider, and only owner/admin roles may decide (brief §4),
  // are both enforced server-side unconditionally (ADR-004); hiding the
  // controls here is a UI-level reflection of those rules, not a substitute
  // for them -- the backend would reject this same actor's attempt
  // regardless. `canDecide` reflects the workspace role check (Codex
  // review, branch 4): members/viewers would otherwise see Approve/Reject
  // buttons that always fail with a 403.
  const isOwnRequest = currentUserId !== null && request.requested_by === currentUserId;

  async function handleDecide(action: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      const updated = await apiPostJson<ApiApprovalRequest>(
        `/api/approvals/requests/${request.id}/${action}`,
      );
      onChanged(updated);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "That action could not be completed.")
          : "Something went wrong. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{request.tool_name}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{APPROVAL_STATUS_LABELS[request.status]}</span>
          {request.decided_at ? (
            <>
              <span>&middot;</span>
              <span>Decided {new Date(request.decided_at).toLocaleString()}</span>
            </>
          ) : null}
        </div>
        {/* Approval and actual completion are two separate async moments
            (dispatch is async, brief §2) -- this must never imply
            "approved" means "done". */}
        {request.execution ? (
          <p className="text-sm">
            Run status:{" "}
            {EXECUTION_STATUS_LABELS[request.execution.status] ?? request.execution.status}
            {request.execution.status === "failed" && request.execution.error_message
              ? ` — ${request.execution.error_message}`
              : null}
            {request.execution.status === "succeeded"
              ? ` (${request.execution.written_count} written, ${request.execution.updated_count} updated, ${request.execution.removed_count} removed)`
              : null}
          </p>
        ) : null}
        {isOwnRequest && request.status === "pending" ? (
          <p className="text-sm text-muted-foreground">
            Waiting for another workspace admin to decide this request.
          </p>
        ) : null}
      </CardContent>
      {request.status === "pending" && !isOwnRequest && canDecide ? (
        <CardFooter className="flex justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleDecide("reject")}
            disabled={busy}
          >
            Reject
          </Button>
          <Button size="sm" onClick={() => void handleDecide("approve")} disabled={busy}>
            Approve
          </Button>
        </CardFooter>
      ) : null}
    </Card>
  );
}
