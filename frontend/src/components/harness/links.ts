/**
 * Routed harness console links (Phase 4 branch 5). Detail links carry
 * `?workspace=` so a link into another firm's job switches the active
 * workspace (WorkspaceProvider) instead of rendering under the wrong one.
 */
function withWorkspace(path: string, workspaceId: string, params: Record<string, string> = {}) {
  const query = new URLSearchParams({ ...params, workspace: workspaceId });
  return `${path}?${query.toString()}`;
}

export function harnessJobHref(job: { id: string; workspace: string }): string {
  return withWorkspace(`/harness/jobs/${encodeURIComponent(job.id)}`, job.workspace);
}

export function implementationJobHref(job: { id: string; workspace: string }): string {
  return withWorkspace(`/harness/implementations/${encodeURIComponent(job.id)}`, job.workspace);
}

/** The console's implementation form, prefilled from a finished discovery job. */
export function implementationRequestHref(discoveryJob: { id: string; workspace: string }): string {
  return withWorkspace("/harness", discoveryJob.workspace, {
    tab: "implementation",
    discovery: discoveryJob.id,
  });
}

export const HARNESS_REPOS_HREF = "/harness/repos";

/** A whole card as a link: keyboard-reachable, focus-visible, opens the routed detail. */
export const CARD_LINK_CLASSES =
  "block rounded-xl transition-colors hover:bg-accent/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none";
