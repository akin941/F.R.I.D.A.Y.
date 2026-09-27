export interface ApiUser {
  id: string;
  email: string;
  name: string;
  is_agent: boolean;
  is_owner: boolean;
}

export interface ApiWorkspaceSummary {
  id: string;
  name: string;
  type: "personal" | "company";
  role: string;
}

export interface ApiWorkspace {
  id: string;
  name: string;
  type: "personal" | "company";
  enabled_modules: string[];
  role: string;
  created_at: string;
}

// --- Firm dossier (Phase 0 §2, Phase 1 p1-firm-dossier UI) ---

export interface ApiWorkspaceProfileRepo {
  name: string;
  url: string;
  default_branch?: string;
}

export interface ApiWorkspaceProfileService {
  name: string;
  kind?: string;
  url?: string;
}

export interface ApiWorkspaceProfileDeployTarget {
  name: string;
  host?: string;
  // Metadata only -- deploy credentials/secrets must NEVER be entered here
  // (backend workspaces/models.py; secrets stay in env per CLAUDE.md).
  notes?: string;
}

export interface ApiWorkspaceProfile {
  repos?: ApiWorkspaceProfileRepo[];
  services?: ApiWorkspaceProfileService[];
  deploy_targets?: ApiWorkspaceProfileDeployTarget[];
  notes?: string;
}

// GET/PATCH /api/workspaces/[workspaceId] -- the only surface that exposes
// `profile`; the workspace list (ApiWorkspace above) deliberately doesn't.
export interface ApiWorkspaceDetail extends ApiWorkspace {
  profile: ApiWorkspaceProfile;
}

// A "new device enrolled remotely" notice (Phase 4 branch 2), shown on every
// device for a week after a remote-key enrollment.
export interface ApiSecurityNotice {
  kind: "remote_enrollment";
  device_id: string;
  device_name: string;
  created_at: string;
}

export interface SessionResponse {
  user: ApiUser;
  workspaces: ApiWorkspaceSummary[];
  security_notices?: ApiSecurityNotice[];
}

// --- Device trust (Phase 4 branch 2) ---

export type ApiDeviceEnrolledVia = "pin" | "strong_code" | "remote_key";

export interface ApiTrustedDevice {
  id: string;
  name: string;
  enrolled_via: ApiDeviceEnrolledVia;
  enrolled_ip: string | null;
  user_agent: string;
  created_at: string;
  last_seen_at: string | null;
  last_seen_ip: string | null;
  revoked_at: string | null;
  revoke_reason: string;
  idle_expires_at: string;
  is_current: boolean;
}

export interface ApiPairingCode {
  code: string;
  kind: "pin" | "strong";
  expires_at: string;
}

export interface ApiRemoteKeyState {
  active: boolean;
  created_at: string | null;
  last_used_at: string | null;
  key?: string;
}

// Which channel a turn arrived over (backend Message.Channel, MVP 9 §1).
// `openai`: the OpenAI-compatible facade (Phase 4 branch 6), e.g. Open WebUI.
export type MessageChannel = "web" | "cli" | "telegram" | "openai";

export type MessageIntent =
  | ""
  | "chat"
  | "memory_query"
  | "knowledge_query"
  | "task_draft"
  | "tool_request";

export interface ApiConversation {
  id: string;
  workspace: string;
  title: string;
  created_by: string | null;
  created_at: string;
  // One-way (false -> true only): a private conversation feeds no memory.
  private: boolean;
  // The first message's channel (Phase 4 branch 4 channel badge); null
  // until the conversation has a message.
  origin_channel: MessageChannel | null;
}

export type MessageRole = "user" | "assistant" | "system";
export type MessageStatus = "complete" | "partial" | "failed";

export interface ApiMessage {
  id: string;
  conversation: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  model_usage: string | null;
  created_by: string | null;
  created_at: string;
  // Ordered memory ids supplied to the model for this reply — "memory
  // context", not proof the model used them (MVP 2 §2).
  memories_used: string[];
  // Same contract, for RAG knowledge chunks (MVP 3 §4): ordered chunk ids
  // supplied as context, not proof of use.
  chunks_used: string[];
  // {memory_id: source_workspace_id}, populated only for entries in
  // memories_used that came from the personal-assistant cross-workspace
  // overlay (Phase 1 p1-personal-overlay); {} otherwise. No UI surface
  // renders this yet -- it's the data a future "from Workspace X" view
  // would need, not a redefinition of memories_used's own contract.
  overlay_source_workspaces: Record<string, string>;
  // The router's classification of a USER message (MVP 6 §1) and its
  // frozen payload: `{title, due_date}` for task_draft, `{tool_name,
  // payload}` for tool_request, `{}` otherwise.
  intent: MessageIntent;
  intent_payload: Record<string, unknown>;
  channel: MessageChannel;
  // The task confirmed from this task-draft message (Task.source_message),
  // or null -- how a reloaded chat knows a draft was already confirmed.
  confirmed_task: string | null;
  // {chunk_id: document_id} for this reply's chunks_used, resolved in the
  // conversation's scope; a chunk whose document is gone is absent.
  chunk_documents: Record<string, string>;
}

export type ChatStreamEvent =
  | { type: "chunk"; text: string }
  | {
      type: "done";
      message_id: string;
      usage?: Record<string, unknown>;
      memories_used: string[];
      chunks_used: string[];
      overlay_source_workspaces: Record<string, string>;
    }
  | { type: "error"; error_type: string }
  // Non-terminal, optional, emitted once (only for a task_draft/tool_request
  // turn with a usable extracted draft) after the streamed chunks and
  // BEFORE `done` -- never instead of it (MVP 6 §3/§4/§5).
  // `source_message_id` is the already-persisted user Message's id; the
  // frontend sends it back verbatim as `draft_message_id` on a task confirm
  // (tool requests carry no such reference -- confirming one calls the
  // approvals endpoint directly, which has no provenance field).
  | {
      type: "draft";
      kind: "task";
      source_message_id: string;
      title: string;
      due_date: string | null;
    }
  | {
      type: "draft";
      kind: "tool";
      source_message_id: string;
      tool_name: string;
      payload: Record<string, unknown>;
    };

// --- Memory (MVP 2) ---

export type MemoryType = "semantic" | "episodic" | "preference" | "procedural" | "work_log";
export type MemorySensitivity = "normal" | "private";
export type MemoryStatus = "pending_approval" | "approved" | "rejected" | "archived";
export type MemorySource = "ai_suggested" | "user_created" | "system_generated";

export interface ApiMemory {
  id: string;
  workspace: string;
  memory_type: MemoryType;
  sensitivity: MemorySensitivity;
  status: MemoryStatus;
  source: MemorySource;
  title: string;
  content: string;
  created_by: string | null;
  source_conversation: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiMemoryExport {
  workspace: string;
  count: number;
  memories: ApiMemory[];
}

export interface ApiErrorBody {
  detail?: string;
  // Stable machine-readable error code (backend core/error_codes.py);
  // resolved to Turkish by lib/i18n/error-codes.ts. A validation error on
  // a field literally named `code` makes this a list -- treated as absent.
  code?: unknown;
  [field: string]: unknown;
}

// --- Knowledge (MVP 3) ---

export type KnowledgeSourceType = "pasted_text" | "file_upload" | "url";
export type KnowledgeStatus = "pending" | "processing" | "ready" | "failed";
export type KnowledgeSensitivity = "normal" | "private"; // identical enum to Memory's

export interface ApiKnowledgeDocument {
  id: string;
  workspace: string;
  source_type: KnowledgeSourceType;
  title: string;
  status: KnowledgeStatus;
  sensitivity: KnowledgeSensitivity;
  mime_type: string;
  byte_size: number;
  source_url: string;
  error_message: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

// --- Tasks (MVP 4) ---

export type TaskStatus = "open" | "done" | "cancelled";
export type TaskPriority = "low" | "normal" | "high";
// Mirrors MemorySource's exact vocabulary (MVP 6 §3). Server-controlled only
// -- never accept this as client input; see TaskCreateForm's `draft_message_id`.
export type TaskSource = "ai_suggested" | "user_created";

export interface ApiTask {
  id: string;
  workspace: string;
  title: string;
  description: string;
  status: TaskStatus;
  priority: TaskPriority;
  due_date: string | null;
  source: TaskSource;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

// --- Approvals (MVP 5) ---

export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired";

// vault_export's ToolSpec.describe_execution shape (tools/export.py) --
// other tools may register a differently-shaped `execution`, or none at
// all (then this field is `null`).
export interface ApiVaultExportExecution {
  status: "pending" | "running" | "succeeded" | "failed";
  error_type: string;
  error_message: string;
  written_count: number;
  updated_count: number;
  removed_count: number;
  retry_count: number;
}

// harness_implement (Gate A) execution shape -- its `status` is the
// implementation job's own status (ImplStatus), NOT a vault-export status.
export interface ApiHarnessImplementExecution {
  status: ImplStatus;
  failure_reason: ImplFailureReason;
  branch_name: string;
}

// harness_push (Gate B) execution shape.
export interface ApiHarnessPushExecution {
  status: ImplStatus;
  failure_reason: ImplFailureReason;
  produced_commit_sha: string;
  push_result: ApiImplPushResult;
}

// Server-built, whitelisted summary of what is being decided
// (`ToolSpec.describe_request`); `null` for tools without that hook. The raw
// `payload` stays unexposed either way.
export interface ApiHarnessImplementDisplaySummary {
  kind: "harness_implement";
  job_id: string;
  goal: string;
  // `repo_name` is only a curator-chosen label; `repo_git_url` is what the
  // agent will actually work against.
  repo_name: string;
  repo_git_url: string;
  branch_name: string;
  agent: string;
  reviewer_agent: string;
}

export interface ApiHarnessPushDisplaySummary {
  kind: "harness_push";
  job_id: string;
  branch_name: string;
  produced_commit_sha: string;
  changed_files: string[];
  changed_files_total: number;
  diff_summary: string;
  test_result: { passed: boolean; command: string; exit_code: number | null } | null;
  review: { verdict: string; summary: string } | null;
}

export type ApiDisplaySummary =
  | ApiHarnessImplementDisplaySummary
  | ApiHarnessPushDisplaySummary
  | null;

export interface ApiApprovalRequest {
  id: string;
  workspace: string;
  tool_name: string;
  status: ApprovalStatus;
  requested_by: string | null;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
  // `payload` is deliberately never included (approvals/serializers.py).
  // Each tool registers its own `execution` shape (or none -- `null`),
  // keyed on `tool_name`: vault_export -> ApiVaultExportExecution,
  // harness_implement -> ApiHarnessImplementExecution, harness_push ->
  // ApiHarnessPushExecution. The UI must branch on `tool_name` before
  // reading any tool-specific field (never assume vault-export counters).
  execution:
    | ApiVaultExportExecution
    | ApiHarnessImplementExecution
    | ApiHarnessPushExecution
    | null;
  // Optional: absent on responses from a backend predating this field.
  display_summary?: ApiDisplaySummary;
}

// --- Agents (MVP 7) ---

export type AgentStatus =
  | "planning"
  | "running"
  | "blocked_on_approval"
  | "blocked_on_confirmation"
  | "completed"
  | "failed"
  | "cancelled";

export type AgentFailureReason =
  | ""
  | "plan_too_large"
  | "plan_empty"
  | "planner_error"
  | "consecutive_failures"
  | "gateway_budget_exceeded";

export type AgentStepKind = "read" | "tool_request" | "task_draft";

export type AgentStepStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "blocked_on_approval"
  | "blocked_on_confirmation"
  | "skipped";

export interface ApiAgentStep {
  id: string;
  agent: string;
  order: number;
  kind: AgentStepKind;
  description: string;
  status: AgentStepStatus;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ApiAgent {
  id: string;
  workspace: string;
  goal: string;
  status: AgentStatus;
  failure_reason: AgentFailureReason;
  created_by: string | null;
  gateway_calls_used: number;
  consecutive_failures: number;
  skill_name: string;
  created_at: string;
  updated_at: string;
}

export interface ApiAgentDetail extends ApiAgent {
  steps: ApiAgentStep[];
}

// --- Integrations (MVP 9) ---

export interface ApiPersonalAccessToken {
  id: string;
  name: string;
  workspace: string;
  last_used_at: string | null;
  revoked_at: string | null;
  // Tokens expire (Phase 4 branch 1); the backend rejects them from here on.
  expires_at: string;
  created_at: string;
}

// Only present on the create response, alongside every ApiPersonalAccessToken
// field above -- the raw token appears exactly once, ever (accounts/views.py).
export interface ApiPersonalAccessTokenCreated extends ApiPersonalAccessToken {
  token: string;
}

export interface ApiTelegramLinkCode {
  code: string;
  expires_at: string;
}

export interface ApiTelegramLinkStatus {
  linked: boolean;
  chat_id: string | null;
  linked_at: string | null;
}

export type HarnessJobStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

export type HarnessJobFailureReason = "" | "timeout" | "runner_error" | "budget_exceeded" | "killed";

export type HarnessAgent = "claude_code" | "codex_cli";

export interface ApiHarnessJob {
  id: string;
  workspace: string;
  repo: string;
  goal: string;
  agent: HarnessAgent;
  sensitivity: string;
  status: HarnessJobStatus;
  failure_reason: HarnessJobFailureReason;
  created_by: string | null;
  started_at: string | null;
  elapsed_seconds: number;
  created_at: string;
  updated_at: string;
}

// `raw_transcript` is the CLI's own low-trust output (brief §3/§4, R2
// mitigation) -- the UI must render it visibly distinguished from
// `summary`/`findings`, never presented as equally trustworthy prose.
// All fields optional: `HarnessJob.briefing` defaults to `{}` at the model
// level (backend/harness/models.py) for any job that hasn't reached
// finalization yet -- a pending/running/errored-before-briefing job's
// `briefing` genuinely has none of these keys, not empty-string/empty-array
// versions of them.
export interface ApiHarnessJobBriefing {
  summary?: string;
  findings?: string[];
  raw_transcript?: string;
}

export interface ApiHarnessJobDetail extends ApiHarnessJob {
  briefing: ApiHarnessJobBriefing;
}

// --- Harness repos (Phase 3) ---

export interface ApiHarnessRepo {
  id: string;
  workspace: string;
  name: string;
  git_url: string;
  default_branch: string;
  runner: string;
  enabled: boolean;
  sensitive: boolean;
  test_command: string;
  created_at: string;
  updated_at: string;
}

// --- Harness implementation jobs (Phase 3 branch 6/7) ---

export type ImplStatus =
  | "pending_gate_a"
  | "implementing"
  | "pending_review"
  | "pending_gate_b"
  | "pushing"
  | "succeeded"
  | "failed"
  | "cancelled";

export type ImplFailureReason =
  | ""
  | "timeout"
  | "runner_error"
  | "budget_exceeded"
  | "killed"
  | "gate_a_rejected"
  | "gate_b_rejected"
  | "gate_expired"
  | "test_command_not_configured"
  | "test_result_unavailable"
  | "tests_failed"
  | "review_changes_requested"
  | "review_unavailable"
  | "worktree_expired"
  | "push_failed"
  | "no_changes"
  | "secret_leak";

// A gate revoked by cancel/worktree expiry shows status "expired".
export type ApiGateSummary = {
  id: string;
  status: ApprovalStatus;
  decided_at: string | null;
} | null;

export interface ApiImplementationJob {
  id: string;
  workspace: string;
  repo: string;
  repo_name: string;
  discovery_job: string | null;
  goal: string;
  agent: "claude_code";
  // "" when no cross-vendor reviewer was selectable.
  reviewer_agent: "codex_cli" | "";
  status: ImplStatus;
  failure_reason: ImplFailureReason;
  cancel_requested: boolean;
  branch_name: string;
  created_by: string | null;
  started_at: string | null;
  elapsed_seconds: number | null;
  created_at: string;
  updated_at: string;
  gate_a: ApiGateSummary;
  gate_b: ApiGateSummary;
}

export interface ApiImplementationReview {
  id: string;
  agent: "codex_cli";
  status: "pending" | "running" | "succeeded" | "failed";
  verdict: "" | "approved" | "changes_requested";
  // Gateway-produced -- Jarvis's own summarization, trusted-ish.
  summary: string;
  findings: string[];
  // The reviewer CLI's own output -- LOW TRUST (R2), rendered only behind
  // an explicit disclosure, never merged with `summary`/`findings`.
  raw_transcript: string;
  elapsed_seconds: number | null;
  created_at: string;
  updated_at: string;
}

// `{}` until the job has run tests / attempted a push.
export type ApiImplTestResult =
  | { ran: boolean; exit_code: number | null; passed: boolean; command: string }
  | Record<string, never>;

export type ApiImplPushResult =
  | { pushed: boolean; branch_name?: string; error?: string }
  | Record<string, never>;

export interface ApiImplementationJobDetail extends ApiImplementationJob {
  base_commit_sha: string;
  produced_commit_sha: string;
  changed_files: string[];
  // Gateway-produced summary of the diff stat (deterministic fallback if
  // the gateway fails) -- a model summary, not the diff itself.
  diff_summary: string;
  test_result: ApiImplTestResult;
  push_result: ApiImplPushResult;
  worktree_expires_at: string | null;
  // The implementing CLI's own output -- LOW TRUST (R2), same rendering
  // discipline as `ApiHarnessJobBriefing.raw_transcript`.
  raw_transcript: string;
  // Newest first.
  reviews: ApiImplementationReview[];
}

// --- Activity summary (Phase 4 branch 3) ---

// GET /api/activity/summary: one row per workspace membership.
export interface ApiActivityWorkspace {
  workspace: string;
  pending_approvals: number;
  live_harness_jobs: number;
  live_agents: number;
}

export interface ApiActivitySummary {
  workspaces: ApiActivityWorkspace[];
  totals: Omit<ApiActivityWorkspace, "workspace">;
}
