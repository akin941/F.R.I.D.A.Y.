/** The routed approval detail (Phase 4 branch 5): `/approvals/[requestId]`. */
export function approvalHref(requestId: string): string {
  return `/approvals/${encodeURIComponent(requestId)}`;
}
