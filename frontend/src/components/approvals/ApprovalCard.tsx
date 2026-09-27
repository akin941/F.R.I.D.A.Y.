"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { StatusPill } from "@/components/ui/status-pill";
import { APPROVAL_STATUS_LABELS } from "@/components/approvals/labels";
import { approvalHref } from "@/components/approvals/links";
import { rendererFor, type Decision } from "@/components/approvals/renderers";
import { apiPostJson } from "@/lib/api-client";
import { formatRemaining } from "@/lib/format/duration";
import { formatDateTime, formatRelativeTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { useCountdown } from "@/lib/use-countdown";
import { cn } from "@/lib/utils";
import type { ApiApprovalRequest } from "@/types/api";

const copy = tr.approvals;

/** The inbox's j/k navigation targets (one per card: its title link). */
export const INBOX_ITEM_ATTRIBUTE = "data-inbox-item";

function ExpiryCountdown({ expiresAt }: { expiresAt: string }) {
  const remaining = useCountdown(expiresAt);
  return (
    <span className={cn(remaining === 0 && "text-destructive")}>
      {remaining > 0 ? copy.meta.expiresIn(formatRemaining(remaining)) : copy.meta.expiredNow}
    </span>
  );
}

function DecisionButtons({
  request,
  subject,
  onChanged,
}: {
  request: ApiApprovalRequest;
  subject: string;
  onChanged: (request: ApiApprovalRequest) => void;
}) {
  const renderer = rendererFor(request.tool_name);

  async function decide(decision: Decision) {
    onChanged(
      await apiPostJson<ApiApprovalRequest>(`/api/approvals/requests/${request.id}/${decision}`),
    );
  }

  function dialog(decision: Decision) {
    const approve = decision === "approve";
    return (
      <ConfirmDialog
        trigger={
          <Button size="sm" variant={approve ? "default" : "outline"}>
            {approve ? copy.decision.approve : copy.decision.reject}
          </Button>
        }
        title={approve ? copy.decision.approveTitle : copy.decision.rejectTitle}
        description={renderer.consequence(request, decision)}
        confirmLabel={approve ? copy.decision.approveConfirm : copy.decision.rejectConfirm}
        busyLabel={copy.decision.busy}
        destructive={!approve}
        errorFallback={copy.decision.failed}
        onConfirm={() => decide(decision)}
      >
        <p className="text-sm font-medium">{subject}</p>
      </ConfirmDialog>
    );
  }

  return (
    <CardFooter className="flex justify-end gap-2">
      {dialog("reject")}
      {dialog("approve")}
    </CardFooter>
  );
}

/**
 * One approval request, rendered by its tool's renderer
 * (`components/approvals/renderers/`, generic fallback for any other tool):
 * what is being decided (`display_summary`), the run status after approval
 * (`execution`), and -- while pending, for a curator -- Onayla/Reddet, each
 * behind a dialog that states the consequence. Decisions only happen here,
 * in the web UI (ADR-008 decision 3); the server enforces the channel and
 * the role unconditionally, `canDecide` only hides controls that would 403.
 *
 * `workspaceName` labels the row in the cross-workspace inbox.
 */
export function ApprovalCard({
  request,
  canDecide,
  onChanged,
  workspaceName,
  linkToDetail = true,
  headingLevel = "h3",
}: {
  request: ApiApprovalRequest;
  canDecide: boolean;
  onChanged: (request: ApiApprovalRequest) => void;
  workspaceName?: string;
  linkToDetail?: boolean;
  headingLevel?: "h1" | "h2" | "h3";
}) {
  const renderer = rendererFor(request.tool_name);
  const title = renderer.title(request);
  const hasOwnTitle = title !== request.tool_name;
  const Heading = headingLevel;
  const origin = request.origin ? (copy.origin[request.origin] ?? request.origin) : null;
  const channel = request.requested_channel
    ? (copy.channel[request.requested_channel] ?? request.requested_channel)
    : null;
  const { Summary, Execution } = renderer;
  const pending = request.status === "pending";

  return (
    <Card id={`approval-${request.id}`} data-testid={`approval-${request.id}`}>
      <CardHeader className="gap-1">
        <CardTitle className="flex flex-wrap items-center gap-2">
          <Heading className="text-base font-medium">
            {linkToDetail ? (
              <Link
                href={approvalHref(request.id)}
                {...{ [INBOX_ITEM_ATTRIBUTE]: "" }}
                className="underline-offset-4 hover:underline focus-visible:rounded-sm focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                {title}
              </Link>
            ) : (
              title
            )}
          </Heading>
          <StatusPill
            domain="approval"
            status={request.status}
            label={APPROVAL_STATUS_LABELS[request.status] ?? request.status}
          />
          {workspaceName ? <Badge variant="outline">{workspaceName}</Badge> : null}
        </CardTitle>
        {hasOwnTitle ? (
          <code className="font-mono text-xs text-muted-foreground">{request.tool_name}</code>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span title={formatDateTime(request.created_at)}>
            {copy.meta.requested(formatRelativeTime(request.created_at))}
          </span>
          {origin && channel ? <span>{copy.meta.via(origin, channel)}</span> : null}
          {request.decided_at ? (
            <span title={formatDateTime(request.decided_at)}>
              {copy.meta.decided(formatRelativeTime(request.decided_at))}
            </span>
          ) : null}
          {pending && request.expires_at ? <ExpiryCountdown expiresAt={request.expires_at} /> : null}
        </div>
        <Summary request={request} />
        {/* Approval and actual completion are two separate async moments
            (dispatch is async) -- this must never imply "approved" means
            "done". */}
        <Execution request={request} />
      </CardContent>
      {pending && canDecide ? (
        <DecisionButtons
          request={request}
          subject={workspaceName ? copy.decision.subject(title, workspaceName) : title}
          onChanged={onChanged}
        />
      ) : null}
    </Card>
  );
}
