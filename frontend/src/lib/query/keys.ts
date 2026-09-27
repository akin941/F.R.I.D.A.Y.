/**
 * Query keys. Every workspace-scoped key STARTS with the workspace id, so
 * switching workspace can only ever read the new workspace's cache entry
 * -- another workspace's cached rows are never rendered (there is no
 * `placeholderData: keepPreviousData` anywhere for the same reason).
 * Invalidating `[workspaceId]` drops everything cached for one workspace.
 *
 * Cross-workspace keys -- the activity summary and the approvals inbox
 * (aggregates over all of the actor's memberships) -- live under
 * `ALL_WORKSPACES`.
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
  harnessRepos: (workspaceId: string) => [workspaceId, "harness", "repos"] as const,
  approvals: (workspaceId: string) => [workspaceId, "approvals"] as const,
  trashTasks: (workspaceId: string) => [workspaceId, "trash", "tasks"] as const,
  trashDocuments: (workspaceId: string) => [workspaceId, "trash", "documents"] as const,
  conversations: (workspaceId: string) => [workspaceId, "conversations"] as const,
  messages: (workspaceId: string, conversationId: string) =>
    [workspaceId, "conversations", conversationId, "messages"] as const,
  telegramLinkStatus: (workspaceId: string) =>
    [workspaceId, "integrations", "telegram", "link-status"] as const,
  activitySummary: () => [ALL_WORKSPACES, "activity", "summary"] as const,
  // The cross-workspace approvals inbox (Phase 4 branch 5): one aggregate
  // over every membership, like the activity summary. Invalidating
  // `allApprovals()` refreshes the inbox under every filter and every
  // cached request detail.
  allApprovals: () => [ALL_WORKSPACES, "approvals"] as const,
  approvalInbox: (filters: Record<string, string>) =>
    [ALL_WORKSPACES, "approvals", "inbox", filters] as const,
  approvalDetail: (requestId: string) => [ALL_WORKSPACES, "approvals", "detail", requestId] as const,
};
