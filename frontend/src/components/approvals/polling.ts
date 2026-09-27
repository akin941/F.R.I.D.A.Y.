import { rendererFor } from "@/components/approvals/renderers";
import { POLL_SLOW_MS, fastestPollInterval } from "@/lib/query/polling";
import type { ApiApprovalRequest } from "@/types/api";

/**
 * How often an approvals view should refresh for this request, or null
 * when it can no longer change on its own: the tool's own execution
 * cadence (renderers), and -- for a still-pending request -- a slow
 * refresh so a decision made elsewhere (another tab or device) or the TTL
 * expiry shows up. Never while the tab is hidden (query client defaults).
 */
export function requestPollInterval(request: ApiApprovalRequest): number | null {
  const execution = rendererFor(request.tool_name).pollInterval(request);
  const pending = request.status === "pending" ? POLL_SLOW_MS : null;
  return fastestPollInterval([execution, pending]);
}

export function listPollInterval(requests: ApiApprovalRequest[]): number | null {
  return fastestPollInterval(requests.map(requestPollInterval));
}
