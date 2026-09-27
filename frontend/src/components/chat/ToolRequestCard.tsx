"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ApprovalCard } from "@/components/approvals/ApprovalCard";
import type { ToolRequestDraft } from "@/components/chat/drafts";
import { apiPostJson, errorMessage } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import type { ApiApprovalRequest } from "@/types/api";

export type { ToolRequestDraft };

function toolTitle(toolName: string): string {
  return tr.approvals.toolTitles[toolName] ?? toolName;
}

export function approvalsHref(workspaceId: string): string {
  return `/approvals?workspace=${encodeURIComponent(workspaceId)}`;
}

/**
 * Confirm/discard card for a `tool_request`-classified chat turn (MVP 6 §4),
 * offered only live, in the tab that received the stream's `draft` event.
 * Confirming calls `approvals.services.create_request` -- the exact function
 * the manual "Export to Obsidian vault" button already calls -- never a
 * bypass of the approval gate. Discard is a pure client-side no-op; nothing
 * was created yet (classification alone never auto-requests). Once created,
 * this renders the same `ApprovalCard` the `/approvals` page uses, with
 * `canDecide=false` on purpose: the decision itself (ADR-008 -- the owner's
 * explicit, separate decision) belongs on the approvals page, not inline in
 * the chat turn that produced the request.
 *
 * `created` is owned by the parent so the created request survives the card
 * moving from the live footer to its reconciled place in the history.
 */
export function ToolRequestCard({
  draft,
  workspaceId,
  created,
  onCreated,
  onDiscard,
}: {
  draft: ToolRequestDraft;
  workspaceId: string;
  created: ApiApprovalRequest | null;
  onCreated: (request: ApiApprovalRequest) => void;
  onDiscard: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
      onCreated(request);
    } catch (err) {
      setError(errorMessage(err, tr.chat.toolDraft.failed));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  if (created) {
    return <ApprovalCard request={created} canDecide={false} onChanged={onCreated} />;
  }

  return (
    <Card size="sm" data-testid="tool-request-card">
      <CardHeader>
        <CardTitle>{tr.chat.toolDraft.title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <p className="text-sm font-medium">{draft.toolName}</p>
        <p className="text-sm text-muted-foreground">{tr.chat.toolDraft.needsApproval}</p>
      </CardContent>
      <CardFooter className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onDiscard} disabled={busy}>
          {tr.chat.toolDraft.discard}
        </Button>
        <Button size="sm" onClick={() => void handleConfirm()} disabled={busy}>
          {busy ? tr.chat.toolDraft.confirming : tr.chat.toolDraft.confirm}
        </Button>
      </CardFooter>
    </Card>
  );
}

/**
 * A tool request rebuilt from history (Phase 4 branch 4): read-only. A
 * persisted message cannot tell whether its request was ever confirmed --
 * approval requests carry no link back to the message (no provenance, so
 * no idempotency) -- so the chat never offers a second confirm here; it
 * points to /approvals, where any request that exists is decided.
 */
export function ToolRequestNotice({
  draft,
  workspaceId,
}: {
  draft: ToolRequestDraft;
  workspaceId: string;
}) {
  return (
    <div
      data-testid="tool-request-notice"
      className="flex flex-col gap-1 rounded-lg border px-3 py-2 text-sm"
    >
      <div className="flex items-center gap-2">
        <Wrench aria-hidden className="size-4 shrink-0 text-muted-foreground" />
        <span className="font-medium">
          {tr.chat.toolDraft.readOnlyTitle(toolTitle(draft.toolName))}
        </span>
      </div>
      <p className="text-muted-foreground">{tr.chat.toolDraft.readOnlyHint}</p>
      <Link
        href={approvalsHref(workspaceId)}
        className="self-start text-primary underline underline-offset-4"
      >
        {tr.chat.toolDraft.openApprovals}
      </Link>
    </div>
  );
}
