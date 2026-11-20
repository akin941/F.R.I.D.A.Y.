"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { approvalHref } from "@/components/approvals/links";
import { SHA_PATTERN, shortSha } from "@/components/deploys/labels";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StatusPill } from "@/components/ui/status-pill";
import { apiGetJson, apiPostJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { ALL_WORKSPACES, queryKeys } from "@/lib/query/keys";
import type { ApiApprovalRequest, ApiDeployBranchHead, ApiDeployTarget } from "@/types/api";

const copy = tr.deploys.dialog;

function HeadInfo({ head }: { head: ApiDeployBranchHead }) {
  return (
    <div className="flex flex-col gap-1 text-sm" data-testid="deploy-head">
      <p className="text-xs text-muted-foreground">
        {head.source === "github" ? copy.prefilled(head.tracked_branch) : copy.manual}
      </p>
      <p className="flex flex-wrap items-center gap-2">
        <StatusPill
          domain="ci"
          status={head.ci_conclusion}
          label={tr.status.ci[head.ci_conclusion] ?? head.ci_conclusion}
        />
        {head.require_ci_success ? (
          <span className="text-xs text-muted-foreground">{copy.ciRequired}</span>
        ) : null}
      </p>
      {head.title ? (
        <p className="text-xs">
          <span className="text-muted-foreground">{copy.commitTitle}: </span>
          <span className="whitespace-pre-wrap break-words">{head.title}</span>
        </p>
      ) : null}
    </div>
  );
}

/**
 * The "Deploy" dialog (brief §9): the SHA prefilled from the tracked
 * branch's head (with its CI state and commit title), editable, then a
 * separate confirmation before anything is sent. Confirming only mints the
 * pending `deploy` gate; the deploy itself waits for the owner's decision
 * on the approvals surface (ADR-008 decision 3).
 */
export function DeployDialog({ target }: { target: ApiDeployTarget }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [sha, setSha] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [created, setCreated] = useState<ApiApprovalRequest | null>(null);

  const head = useQuery({
    queryKey: queryKeys.deployTargetHead(target.workspace, target.id),
    queryFn: () => apiGetJson<ApiDeployBranchHead>(`/api/deploys/targets/${target.id}/head`),
    enabled: open,
    staleTime: 0,
  });

  // The prefill until the owner types: never overwrite their own input.
  const value = (sha ?? head.data?.sha ?? "").trim().toLowerCase();
  const validSha = SHA_PATTERN.test(value);
  const isLive = validSha && target.current_sha.startsWith(value);
  const shaError = touched && !validSha ? copy.shaInvalid : isLive ? copy.shaIsLive : null;

  function reset(next: boolean) {
    setOpen(next);
    if (!next) {
      setSha(null);
      setTouched(false);
      setCreated(null);
    }
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (!validSha || isLive) return;
    setConfirming(true);
  }

  async function requestDeploy() {
    const approval = await apiPostJson<ApiApprovalRequest>("/api/deploys/requests", {
      target: target.id,
      sha: value,
    });
    setCreated(approval);
    void queryClient.invalidateQueries({ queryKey: queryKeys.allApprovals() });
    void queryClient.invalidateQueries({ queryKey: [ALL_WORKSPACES, "activity"] });
  }

  return (
    <>
      <Dialog open={open} onOpenChange={reset}>
        <DialogTrigger asChild>
          <Button size="sm" disabled={!target.enabled || target.active_run != null}>
            {tr.deploys.deploy}
          </Button>
        </DialogTrigger>
        <DialogContent>
          {created ? (
            <div className="flex flex-col gap-4">
              <DialogHeader>
                <DialogTitle>{copy.created}</DialogTitle>
                <DialogDescription>{copy.description}</DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button asChild>
                  <Link href={approvalHref(created.id)}>{copy.openApproval}</Link>
                </Button>
              </DialogFooter>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
              <DialogHeader>
                <DialogTitle>{copy.title(target.name)}</DialogTitle>
                <DialogDescription>{copy.description}</DialogDescription>
              </DialogHeader>
              {head.isPending ? (
                <p className="text-sm text-muted-foreground">{copy.loadingHead}</p>
              ) : head.data ? (
                <HeadInfo head={head.data} />
              ) : (
                <p className="text-sm text-muted-foreground">{copy.manual}</p>
              )}
              <div className="flex flex-col gap-2">
                <Label htmlFor={`deploy-sha-${target.id}`}>{copy.shaLabel}</Label>
                <Input
                  id={`deploy-sha-${target.id}`}
                  value={sha ?? head.data?.sha ?? ""}
                  onChange={(event) => setSha(event.target.value)}
                  onBlur={() => setTouched(true)}
                  autoComplete="off"
                  spellCheck={false}
                  className="font-mono"
                  aria-invalid={shaError ? true : undefined}
                  aria-describedby={`deploy-sha-hint-${target.id}`}
                />
                <p id={`deploy-sha-hint-${target.id}`} className="text-xs text-muted-foreground">
                  {copy.shaHint(target.tracked_branch)}
                </p>
                {shaError ? (
                  <Alert variant="destructive">
                    <AlertDescription>{shaError}</AlertDescription>
                  </Alert>
                ) : null}
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => reset(false)}>
                  {tr.common.cancel}
                </Button>
                <Button type="submit">{copy.submit}</Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={copy.confirmTitle}
        description={copy.confirmDescription(shortSha(value), `${target.name} (${target.environment})`)}
        confirmLabel={copy.confirm}
        busyLabel={copy.busy}
        destructive={false}
        errorFallback={copy.failed}
        onConfirm={requestDeploy}
      >
        <code className="block font-mono text-xs break-all">{value}</code>
      </ConfirmDialog>
    </>
  );
}
