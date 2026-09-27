/**
 * Query keys. Every workspace-scoped key STARTS with the workspace id, so
 * switching workspace can only ever read the new workspace's cache entry
 * -- another workspace's cached rows are never rendered (there is no
 * `placeholderData: keepPreviousData` anywhere for the same reason).
 * Invalidating `[workspaceId]` drops everything cached for one workspace.
 *
 * The only cross-workspace key is the activity summary (one aggregate over
 * all of the actor's memberships), under `ALL_WORKSPACES`.
 */
export const ALL_WORKSPACES = "*all-workspaces*";

export const queryKeys = {
  workspace: (workspaceId: string) => [workspaceId] as const,
  agents: (workspaceId: string) => [workspaceId, "agents"] as const,
  agent: (workspaceId: string, agentId: string) => [workspaceId, "agents", agentId] as const,
  harnessJobs: (workspaceId: string) => [workspaceId, "harness", "jobs"] as const,
  harnessJob: (workspaceId: string, jobId: string) =>
    [workspaceId, "harness", "jobs", jobId] as const,
  implementationJobs: (workspaceId: string) =>
    [workspaceId, "harness", "implementation-jobs"] as const,
  implementationJob: (workspaceId: string, jobId: string) =>
    [workspaceId, "harness", "implementation-jobs", jobId] as const,
  approvals: (workspaceId: string) => [workspaceId, "approvals"] as const,
  conversations: (workspaceId: string) => [workspaceId, "conversations"] as const,
  messages: (workspaceId: string, conversationId: string) =>
    [workspaceId, "conversations", conversationId, "messages"] as const,
  telegramLinkStatus: (workspaceId: string) =>
    [workspaceId, "integrations", "telegram", "link-status"] as const,
  activitySummary: () => [ALL_WORKSPACES, "activity", "summary"] as const,
};
