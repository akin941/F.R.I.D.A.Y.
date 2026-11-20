import { tr } from "@/lib/i18n/tr";
import { POLL_FAST_MS, POLL_SLOW_MS } from "@/lib/query/polling";
import type { DeployRunStatus } from "@/types/api";

export const DEPLOY_RUN_STATUS_LABELS: Record<DeployRunStatus, string> = tr.status.deployRun;

/** Still moving on its own (backend `DeployRun.ACTIVE_STATUSES`). */
export const DEPLOY_ACTIVE_STATUSES: readonly string[] = [
  "pending",
  "checking",
  "deploying",
  "health_checking",
  "rolling_back",
];

export function isDeployActive(status: string): boolean {
  return DEPLOY_ACTIVE_STATUSES.includes(status);
}

/** Fast while a run moves; a `pending` run may wait on an offline runner. */
export function deployPollInterval(status: string): number | null {
  if (status === "pending") return POLL_SLOW_MS;
  return isDeployActive(status) ? POLL_FAST_MS : null;
}

export function deployErrorLabel(code: string): string {
  if (!code) return "";
  return tr.deploys.errorCodes[code] ?? code;
}

export function shortSha(sha: string | null | undefined, length = 12): string {
  return (sha ?? "").slice(0, length);
}

/** A full 40-char SHA or an abbreviation of 7+ hex characters. */
export const SHA_PATTERN = /^[0-9a-f]{7,40}$/;

export const DEPLOYS_HREF = "/deploys";

export function deployRunHref(run: { id: string; workspace: string }): string {
  const query = new URLSearchParams({ workspace: run.workspace });
  return `/deploys/runs/${encodeURIComponent(run.id)}?${query.toString()}`;
}
