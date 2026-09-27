"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  APPROVAL_STATUS_LABELS,
  EXECUTION_STATUS_LABELS,
  TOOL_TITLES,
  decisionConsequence,
  isHarnessTool,
} from "@/components/approvals/labels";
import {
  HARNESS_AGENT_LABELS,
  IMPL_STATUS_LABELS,
  REVIEW_VERDICT_LABELS,
  harnessFailureReasonLabel,
} from "@/components/harness/labels";
import { apiPostJson, ApiError } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import type {
  ApiApprovalRequest,
  ApiHarnessImplementDisplaySummary,
  ApiHarnessImplementExecution,
  ApiHarnessPushDisplaySummary,
  ApiHarnessPushExecution,
  ApiVaultExportExecution,
} from "@/types/api";

const CHANGED_FILES_PREVIEW = 20;

function agentLabel(agent: string): string {
  return HARNESS_AGENT_LABELS[agent as keyof typeof HARNESS_AGENT_LABELS] ?? agent;
}

function SummaryRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
      <dt className="shrink-0 text-muted-foreground sm:w-32">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

function ImplementSummary({ summary }: { summary: ApiHarnessImplementDisplaySummary }) {
  return (
    <dl className="flex flex-col gap-1 text-sm">
      <SummaryRow label="Hedef">
        <span className="whitespace-pre-wrap">{summary.goal}</span>
      </SummaryRow>
      <SummaryRow label="Depo">{summary.repo_name}</SummaryRow>
      <SummaryRow label="Dal">
        <code className="font-mono">{summary.branch_name}</code>
      </SummaryRow>
      <SummaryRow label="Uygulayıcı">{agentLabel(summary.agent)}</SummaryRow>
      <SummaryRow label="İnceleyici">{agentLabel(summary.reviewer_agent)}</SummaryRow>
    </dl>
  );
}

function PushSummary({ summary }: { summary: ApiHarnessPushDisplaySummary }) {
  const shown = summary.changed_files.slice(0, CHANGED_FILES_PREVIEW);
  const total = Math.max(summary.changed_files_total, summary.changed_files.length);
  const more = total - shown.length;
  return (
    <dl className="flex flex-col gap-1 text-sm">
      <SummaryRow label="Dal">
        <code className="font-mono">{summary.branch_name}</code>
      </SummaryRow>
      {summary.produced_commit_sha ? (
        <SummaryRow label="Commit">
          <code className="font-mono" title={summary.produced_commit_sha}>
            {summary.produced_commit_sha.slice(0, 12)}
          </code>
        </SummaryRow>
      ) : null}
      <SummaryRow label={`Değişen dosyalar (${total})`}>
        <ul className="font-mono text-xs">
          {shown.map((file) => (
            <li key={file} className="break-all">
              {file}
            </li>
          ))}
          {more > 0 ? <li className="font-sans text-muted-foreground">+{more} daha</li> : null}
        </ul>
      </SummaryRow>
      {summary.diff_summary ? (
        <SummaryRow label="Değişiklik özeti">
          <span className="block text-xs text-muted-foreground">
            Model tarafından üretilmiş özet — diff’in kendisi değildir.
          </span>
          <span className="whitespace-pre-wrap">{summary.diff_summary}</span>
        </SummaryRow>
      ) : null}
      <SummaryRow label="Testler">
        {summary.test_result ? (
          <span className={cn(!summary.test_result.passed && "text-destructive")}>
            {summary.test_result.passed ? "Geçti" : "Başarısız"}
            {summary.test_result.exit_code != null
              ? ` (çıkış kodu ${summary.test_result.exit_code})`
              : ""}
            {" — "}
            <code className="font-mono">{summary.test_result.command}</code>
          </span>
        ) : (
          <span className="text-muted-foreground">Sonuç yok</span>
        )}
      </SummaryRow>
      <SummaryRow label="İnceleme">
        {summary.review ? (
          <span>
            <span
              className={cn(
                "font-medium",
                summary.review.verdict === "changes_requested" && "text-destructive",
              )}
            >
              {REVIEW_VERDICT_LABELS[summary.review.verdict] ?? summary.review.verdict}
            </span>
            {summary.review.summary ? (
              <span className="block whitespace-pre-wrap">{summary.review.summary}</span>
            ) : null}
          </span>
        ) : (
          <span className="text-muted-foreground">İnceleme yok</span>
        )}
      </SummaryRow>
    </dl>
  );
}

function VaultExportExecutionLine({ execution }: { execution: ApiVaultExportExecution }) {
  const hasCounters =
    typeof execution.written_count === "number" &&
    typeof execution.updated_count === "number" &&
    typeof execution.removed_count === "number";
  return (
    <p className="text-sm">
      Çalışma durumu: {EXECUTION_STATUS_LABELS[execution.status] ?? execution.status}
      {execution.status === "failed" && execution.error_message
        ? ` — ${execution.error_message}`
        : null}
      {execution.status === "succeeded" && hasCounters
        ? ` (${execution.written_count} yazıldı, ${execution.updated_count} güncellendi, ${execution.removed_count} silindi)`
        : null}
    </p>
  );
}

function HarnessExecutionLine({
  execution,
}: {
  execution: ApiHarnessImplementExecution | ApiHarnessPushExecution;
}) {
  const pushResult = "push_result" in execution ? execution.push_result : null;
  return (
    <div className="flex flex-col gap-0.5 text-sm">
      <p>
        İş durumu:{" "}
        {IMPL_STATUS_LABELS[execution.status as keyof typeof IMPL_STATUS_LABELS] ??
          execution.status}
        {execution.failure_reason
          ? ` — ${harnessFailureReasonLabel(execution.failure_reason)}`
          : null}
      </p>
      {pushResult && "pushed" in pushResult ? (
        pushResult.pushed ? (
          <p>Dal uzak depoya gönderildi.</p>
        ) : pushResult.error ? (
          <p className="text-destructive">Gönderim hatası: {pushResult.error}</p>
        ) : null
      ) : null}
    </div>
  );
}

function GenericExecutionLine({ status }: { status: unknown }) {
  if (typeof status !== "string") return null;
  return <p className="text-sm">Çalışma durumu: {EXECUTION_STATUS_LABELS[status] ?? status}</p>;
}

/**
 * One approval request. Rendering is keyed on `tool_name`: each tool
 * registers its own `execution` shape and (for harness tools) a
 * server-built `display_summary` -- nothing tool-specific is read without
 * first checking which tool this is, so e.g. a harness execution never
 * prints vault-export counters.
 *
 * Deciding is a two-step action: Onayla/Reddet first shows what the
 * decision will do, and only "Evet, …" sends it.
 */
export function ApprovalCard({
  request,
  canDecide,
  onChanged,
  highlighted = false,
}: {
  request: ApiApprovalRequest;
  canDecide: boolean;
  onChanged: (request: ApiApprovalRequest) => void;
  highlighted?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<"approve" | "reject" | null>(null);

  // ADR-008 decision 3 (single-owner): the gate is an explicit, separate,
  // audited decision over the high-trust web channel -- the owner decides
  // their own and their agents' requests, so no own-request hiding here.
  // `canDecide` reflects the workspace role check, enforced server-side
  // unconditionally; hiding for members/viewers is a UI-level reflection
  // of that rule, not a substitute for it.

  async function handleDecide(action: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      const updated = await apiPostJson<ApiApprovalRequest>(
        `/api/approvals/requests/${request.id}/${action}`,
      );
      setConfirming(null);
      onChanged(updated);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "İşlem tamamlanamadı.")
          : "Bir şeyler ters gitti. Lütfen tekrar deneyin.",
      );
    } finally {
      setBusy(false);
    }
  }

  const title = TOOL_TITLES[request.tool_name];
  const summary = request.display_summary ?? null;
  const execution = request.execution;

  return (
    <Card
      id={`approval-${request.id}`}
      tabIndex={highlighted ? -1 : undefined}
      data-highlighted={highlighted ? "true" : undefined}
      className={cn(highlighted && "ring-2 ring-primary outline-none")}
    >
      <CardHeader>
        <CardTitle>{title ?? request.tool_name}</CardTitle>
        {title ? (
          <code className="font-mono text-xs text-muted-foreground">{request.tool_name}</code>
        ) : null}
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
              <span>Karara bağlandı {new Date(request.decided_at).toLocaleString()}</span>
            </>
          ) : null}
        </div>

        {summary?.kind === "harness_implement" && request.tool_name === "harness_implement" ? (
          <ImplementSummary summary={summary} />
        ) : summary?.kind === "harness_push" && request.tool_name === "harness_push" ? (
          <PushSummary summary={summary} />
        ) : isHarnessTool(request.tool_name) ? (
          <p className="text-sm text-muted-foreground">Bu istek için özet bilgisi yok.</p>
        ) : null}

        {/* Approval and actual completion are two separate async moments
            (dispatch is async, brief §2) -- this must never imply
            "approved" means "done". */}
        {execution ? (
          request.tool_name === "vault_export" ? (
            <VaultExportExecutionLine execution={execution as ApiVaultExportExecution} />
          ) : isHarnessTool(request.tool_name) ? (
            <HarnessExecutionLine
              execution={execution as ApiHarnessImplementExecution | ApiHarnessPushExecution}
            />
          ) : (
            <GenericExecutionLine status={(execution as { status?: unknown }).status} />
          )
        ) : null}
      </CardContent>
      {request.status === "pending" && canDecide ? (
        confirming ? (
          <CardFooter className="flex flex-col items-stretch gap-2">
            <p className="text-sm" role="status">
              {decisionConsequence(request.tool_name, confirming)}
            </p>
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirming(null)}
                disabled={busy}
              >
                Vazgeç
              </Button>
              <Button
                size="sm"
                variant={confirming === "reject" ? "destructive" : "default"}
                onClick={() => void handleDecide(confirming)}
                disabled={busy}
              >
                {confirming === "approve" ? "Evet, onayla" : "Evet, reddet"}
              </Button>
            </div>
          </CardFooter>
        ) : (
          <CardFooter className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setConfirming("reject")}>
              Reddet
            </Button>
            <Button size="sm" onClick={() => setConfirming("approve")}>
              Onayla
            </Button>
          </CardFooter>
        )
      ) : null}
    </Card>
  );
}
