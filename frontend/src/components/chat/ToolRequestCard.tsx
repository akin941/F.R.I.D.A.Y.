"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import { apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiApprovalRequest } from "@/types/api";

export interface ToolRequestDraft {
  sourceMessageId: string;
  toolName: string;
  payload: Record<string, unknown>;
}

/**
 * Confirm/discard card for a `tool_request`-classified chat turn (MVP 6 §4).
 * Confirming calls `approvals.services.create_request` -- the exact function
 * the manual "Export to Obsidian vault" button already calls -- never a
 * bypass of the approval gate. Discard is a pure client-side no-op; nothing
 * was created yet (classification alone never auto-requests). Once created,
 * this renders the same `ApprovalCard` the `/approvals` page uses for
 * run-status, since the requester (this user) can never decide their own
 * request anyway -- `ApprovalCard`'s own `isOwnRequest` gating already
 * hides Approve/Reject regardless of `canDecide`.
 */
export function ToolRequestCard({
  draft,
  workspaceId,
  currentUserId,
  onDiscard,
}: {
  draft: ToolRequestDraft;
  workspaceId: string;
  currentUserId: string | null;
  onDiscard: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ApiApprovalRequest | null>(null);
  // Synchronous double-invocation guard (mirrors ChatView's sendingRef/
  // creatingChatRef): `busy` state alone can't stop two confirm clicks
  // dispatched in the same tick, since a state update is batched and
  // doesn't reach the disabled-button render before the second click fires
  // -- a ref closes that window (Codex round-1 review, MEDIUM).
  const busyRef = useRef(false);

  async function handleConfirm() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      const request = await apiPostJson<ApiApprovalRequest>("/api/approvals/requests", {
        workspace: workspaceId,
        tool_name: draft.toolName,
        payload: draft.payload,
      });
      setCreated(request);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "That request could not be created.")
          : "Something went wrong. Please try again.",
      );
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  if (created) {
    return (
      <div className="m-4">
        <ApprovalCard
          request={created}
          currentUserId={currentUserId}
          canDecide={false}
          onChanged={setCreated}
        />
      </div>
    );
  }

  return (
    <Card className="m-4" data-testid="tool-request-card">
      <CardHeader>
        <CardTitle>Looks like a tool request</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <p className="text-sm font-medium">{draft.toolName}</p>
        <p className="text-sm text-muted-foreground">
          This requires another workspace admin&apos;s approval before it runs.
        </p>
      </CardContent>
      <CardFooter className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onDiscard} disabled={busy}>
          Discard
        </Button>
        <Button size="sm" onClick={() => void handleConfirm()} disabled={busy}>
          {busy ? "Requesting…" : "Confirm request"}
        </Button>
      </CardFooter>
    </Card>
  );
}
