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
import type { ApiApprovalRequest, ApiNodeActionExecution } from "@/types/api";

export type { ToolRequestDraft };

const NODE_ACTION = "node_action";
// ADR-014 decision 2 / brief D8: the only confirm-class capabilities.
export const CONFIRM_CLASS_CAPABILITIES = new Set(["apps.open", "apps.focus"]);

export interface NodeActionDraft {
  nodeId: string;
  capability: string;
  args: Record<string, unknown>;
  confirmClass: boolean;
  repoName: string;
  commandKind: string;
  recipe: string;
  bundleId: string;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * A `node_action` draft's fields, read as data (the router's output is
 * untrusted; the server validates everything again). Only the repo's last
 * path segment is shown, never the raw path.
 */
export function nodeActionDraft(draft: ToolRequestDraft): NodeActionDraft {
  const payload = draft.payload;
  const rawArgs = payload.args;
  const args =
    rawArgs && typeof rawArgs === "object" && !Array.isArray(rawArgs) ? (rawArgs as Record<string, unknown>) : {};
  const params = args.params && typeof args.params === "object" ? (args.params as Record<string, unknown>) : {};
  const repoPath = str(args.repo_path) || str(params.repo_path);
  const capability = str(payload.capability);
  return {
    nodeId: str(payload.node_id),
    capability,
    args,
    confirmClass: CONFIRM_CLASS_CAPABILITIES.has(capability),
    repoName: repoPath.replace(/\/+$/, "").split("/").pop() ?? "",
    commandKind: str(args.command_kind),
    recipe: str(args.recipe),
    bundleId: str(args.bundle_id),
  };
}

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

  const nodeAction = draft.toolName === NODE_ACTION ? nodeActionDraft(draft) : null;
  const [sent, setSent] = useState<ApiNodeActionExecution | null>(null);

  async function handleConfirm() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      if (nodeAction?.confirmClass) {
        // Confirm-class (D8): this click is the on-screen act; the Mac's
        // window click is the second. No approval request exists for it.
        if (!nodeAction.nodeId) throw new Error("node_id");
        const execution = await apiPostJson<ApiNodeActionExecution>(
          `/api/nodes/${encodeURIComponent(nodeAction.nodeId)}/actions`,
          {
            capability: nodeAction.capability,
            args: nodeAction.args,
            source_message_id: draft.sourceMessageId,
          },
        );
        setSent(execution);
        return;
      }
      const request = await apiPostJson<ApiApprovalRequest>("/api/approvals/requests", {
        workspace: workspaceId,
        tool_name: draft.toolName,
        payload: nodeAction ? { ...draft.payload, source_message_id: draft.sourceMessageId } : draft.payload,
      });
      onCreated(request);
    } catch (err) {
      setError(
        errorMessage(err, nodeAction?.confirmClass ? tr.chat.toolDraft.nodeAction.failed : tr.chat.toolDraft.failed),
      );
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  if (created) {
    return <ApprovalCard request={created} canDecide={false} onChanged={onCreated} />;
  }

  if (sent) {
    return (
      <Card size="sm" data-testid="node-action-sent">
        <CardContent className="flex flex-col gap-1 text-sm">
          <p className="font-medium">{tr.chat.toolDraft.nodeAction.sent}</p>
          <p className="text-muted-foreground">
            {tr.chat.toolDraft.nodeAction.sentStatus(tr.hud.terminal.statuses[sent.status] ?? sent.status)}
          </p>
        </CardContent>
      </Card>
    );
  }

  if (nodeAction) {
    const copy = tr.chat.toolDraft.nodeAction;
    return (
      <Card size="sm" data-testid="tool-request-card" data-node-class={nodeAction.confirmClass ? "confirm" : "gate"}>
        <CardHeader>
          <CardTitle>{copy.title(nodeAction.capability)}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1">
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <dl className="flex flex-col gap-0.5 text-sm" data-testid="node-action-draft-summary">
            {nodeAction.repoName ? (
              <div className="flex gap-2">
                <dt className="text-muted-foreground">{copy.repo}</dt>
                <dd>{nodeAction.repoName}</dd>
              </div>
            ) : null}
            {nodeAction.commandKind ? (
              <div className="flex gap-2">
                <dt className="text-muted-foreground">{copy.command}</dt>
                <dd>
                  <code className="font-mono">{nodeAction.commandKind}</code>
                </dd>
              </div>
            ) : null}
            {nodeAction.recipe ? (
              <div className="flex gap-2">
                <dt className="text-muted-foreground">{copy.recipe}</dt>
                <dd>{nodeAction.recipe}</dd>
              </div>
            ) : null}
            {nodeAction.bundleId ? (
              <div className="flex gap-2">
                <dt className="text-muted-foreground">{copy.app}</dt>
                <dd>
                  <code className="font-mono">{nodeAction.bundleId}</code>
                </dd>
              </div>
            ) : null}
          </dl>
          <p className="text-sm text-muted-foreground">
            {nodeAction.confirmClass ? copy.confirmWhereToAct : copy.whereToAct}
          </p>
          <p className="text-xs text-muted-foreground">{copy.macNote}</p>
        </CardContent>
        <CardFooter className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onDiscard} disabled={busy}>
            {tr.chat.toolDraft.discard}
          </Button>
          <Button size="sm" onClick={() => void handleConfirm()} disabled={busy}>
            {nodeAction.confirmClass
              ? busy
                ? copy.sending
                : copy.sendToMac
              : busy
                ? tr.chat.toolDraft.confirming
                : tr.chat.toolDraft.confirm}
          </Button>
        </CardFooter>
      </Card>
    );
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
