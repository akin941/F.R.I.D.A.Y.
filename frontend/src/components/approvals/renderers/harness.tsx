import Link from "next/link";
import { NoSummary, SummaryRow, toolTitle } from "@/components/approvals/renderers/shared";
import type { ApprovalRenderer } from "@/components/approvals/renderers/types";
import {
  HARNESS_AGENT_LABELS,
  IMPL_STATUS_LABELS,
  REVIEW_VERDICT_LABELS,
  harnessFailureReasonLabel,
  implPollInterval,
} from "@/components/harness/labels";
import { implementationJobHref } from "@/components/harness/links";
import { TechnicalDetail } from "@/components/harness/TechnicalDetail";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";
import type {
  ApiApprovalRequest,
  ApiHarnessImplementDisplaySummary,
  ApiHarnessImplementExecution,
  ApiHarnessPushDisplaySummary,
  ApiHarnessPushExecution,
} from "@/types/api";

const CHANGED_FILES_PREVIEW = 20;

// "" (e.g. no reviewer was selectable) renders as a dash, never blank.
function agentLabel(agent: string): string {
  if (!agent) return "—";
  return HARNESS_AGENT_LABELS[agent as keyof typeof HARNESS_AGENT_LABELS] ?? agent;
}

function implementSummary(request: ApiApprovalRequest): ApiHarnessImplementDisplaySummary | null {
  const summary = request.display_summary;
  return summary?.kind === "harness_implement" && request.tool_name === "harness_implement"
    ? summary
    : null;
}

function pushSummary(request: ApiApprovalRequest): ApiHarnessPushDisplaySummary | null {
  const summary = request.display_summary;
  return summary?.kind === "harness_push" && request.tool_name === "harness_push"
    ? summary
    : null;
}

function JobLink({ request, jobId }: { request: ApiApprovalRequest; jobId: string }) {
  return (
    <Link
      href={implementationJobHref({ id: jobId, workspace: request.workspace })}
      className="self-start text-xs underline underline-offset-4"
    >
      {tr.approvals.openJob}
    </Link>
  );
}

function ImplementSummary({ request }: { request: ApiApprovalRequest }) {
  const summary = implementSummary(request);
  if (!summary) return <NoSummary />;
  return (
    <div className="flex flex-col gap-1">
      <dl className="flex flex-col gap-1 text-sm">
        <SummaryRow label="Hedef">
          <span className="whitespace-pre-wrap">{summary.goal}</span>
        </SummaryRow>
        <SummaryRow label="Depo">
          {summary.repo_name}
          {/* The name is only a curator-chosen label -- the URL is what the
              agent will actually clone and work against. */}
          {summary.repo_git_url ? (
            <code className="block font-mono text-xs break-all text-muted-foreground">
              {summary.repo_git_url}
            </code>
          ) : null}
        </SummaryRow>
        <SummaryRow label="Dal">
          <code className="font-mono">{summary.branch_name}</code>
        </SummaryRow>
        <SummaryRow label="Uygulayıcı">{agentLabel(summary.agent)}</SummaryRow>
        <SummaryRow label="İnceleyici">{agentLabel(summary.reviewer_agent)}</SummaryRow>
      </dl>
      <JobLink request={request} jobId={summary.job_id} />
    </div>
  );
}

function PushSummary({ request }: { request: ApiApprovalRequest }) {
  const summary = pushSummary(request);
  if (!summary) return <NoSummary />;
  const shown = summary.changed_files.slice(0, CHANGED_FILES_PREVIEW);
  const total = Math.max(summary.changed_files_total, summary.changed_files.length);
  const more = total - shown.length;
  return (
    <div className="flex flex-col gap-1">
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
            {more > 0 ? (
              <li className="font-sans text-muted-foreground">{tr.approvals.more(more)}</li>
            ) : null}
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
                <>
                  <span className="block text-xs text-muted-foreground">
                    Jarvis&apos;in kendi özeti (model üretimi).
                  </span>
                  <span className="block whitespace-pre-wrap">{summary.review.summary}</span>
                </>
              ) : null}
            </span>
          ) : (
            <span className="text-muted-foreground">İnceleme yok</span>
          )}
        </SummaryRow>
      </dl>
      <JobLink request={request} jobId={summary.job_id} />
    </div>
  );
}

function HarnessExecutionLine({ request }: { request: ApiApprovalRequest }) {
  const execution = request.execution as
    | ApiHarnessImplementExecution
    | ApiHarnessPushExecution
    | null;
  if (!execution) return null;
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
        ) : (
          <div className="flex flex-col gap-1">
            <p className="text-destructive">Gönderim başarısız.</p>
            {pushResult.error ? <TechnicalDetail text={pushResult.error} /> : null}
          </div>
        )
      ) : null}
    </div>
  );
}

/**
 * Harness executions report the implementation job's own status: fast
 * while a container is working, slow while the job merely waits at a gate
 * for a human, none once terminal.
 */
function harnessPollInterval(request: ApiApprovalRequest): number | null {
  const status = (request.execution as { status?: unknown } | null)?.status;
  return typeof status === "string" ? implPollInterval(status) : null;
}

/** Gate A: the plan (goal, repo URL, branch, agents) the owner approves. */
export const harnessImplementRenderer: ApprovalRenderer = {
  toolName: "harness_implement",
  title: (request) => toolTitle(request.tool_name),
  Summary: ImplementSummary,
  Execution: HarnessExecutionLine,
  consequence: (request, decision) => {
    const copy = tr.approvals.consequence.harness_implement;
    const summary = implementSummary(request);
    if (decision === "approve" && summary?.branch_name) {
      return copy.approveWith(summary.branch_name, summary.repo_name || summary.repo_git_url);
    }
    return copy[decision];
  },
  pollInterval: harnessPollInterval,
};

/** Gate B: the tested, reviewed change whose branch will be pushed. */
export const harnessPushRenderer: ApprovalRenderer = {
  toolName: "harness_push",
  title: (request) => toolTitle(request.tool_name),
  Summary: PushSummary,
  Execution: HarnessExecutionLine,
  consequence: (request, decision) => {
    const copy = tr.approvals.consequence.harness_push;
    const summary = pushSummary(request);
    if (decision === "approve" && summary?.branch_name) {
      return copy.approveWith(summary.branch_name);
    }
    return copy[decision];
  },
  pollInterval: harnessPollInterval,
};
