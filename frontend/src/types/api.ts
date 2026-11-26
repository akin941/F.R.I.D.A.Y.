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
  // ADR-009 retention window (backend TRASH_RETENTION_DAYS).
  trash_retention_days?: number;
}

// --- Device trust (Phase 4 branch 2) ---

export type ApiDeviceEnrolledVia = "pin" | "strong_code" | "remote_key";

/** Phase 9 D1: a browser, or a Friday Node (the Mac app, `kind=node`). */
export type ApiDeviceKind = "browser" | "node";

export interface ApiTrustedDevice {
  id: string;
  name: string;
  kind: ApiDeviceKind;
  enrolled_via: ApiDeviceEnrolledVia;
  enrolled_ip: string | null;
  user_agent: string;
  created_at: string;
  last_seen_at: string | null;
  last_seen_ip: string | null;
  revoked_at: string | null;
  revoke_reason: string;
  idle_expires_at: string;
  /** A node's hard age cap (null for a browser). */
  credential_expires_at: string | null;
  secret_issued_at: string | null;
  is_current: boolean;
}

export interface ApiPairingCode {
  code: string;
  kind: "pin" | "strong";
  device_kind?: ApiDeviceKind;
  expires_at: string;
}

// --- Friday Nodes (Phase 9 D11, branch 7) ---

/** `nodes.selectors.node_status` (+ `never_paired` on the HUD line). */
export type ApiNodeStatus =
  | "connected"
  | "disconnected"
  | "kill_switch"
  | "revoked"
  | "credential_expiring"
  | "never_paired";

/** `GET /api/v1/nodes` item: codes, counts, short hashes and times only. */
export interface ApiNodeSummary {
  id: string;
  name: string;
  status: Exclude<ApiNodeStatus, "never_paired">;
  online: boolean;
  protocol_version: number;
  node_version: string;
  os_version: string;
  last_heartbeat_at: string | null;
  spool_count: number;
  spool_bytes: number;
  paired_at: string;
  revoked_at: string | null;
  credential_age_days: number;
  credential_expires_at: string | null;
  rotate_after_days: number;
  pinned_key_id: string;
  catalogue_sha256: string;
  advertised_count: number;
  invocable_count: number;
  snapshot_at: string | null;
  /** Phase 13 D3: the owner's labels (server-side data; never sent to the node). */
  name_key?: string;
  room?: ApiRoomRef | null;
  role?: ApiNodeRole;
  platform?: ApiNodePlatform;
  aliases?: string[];
  last_active_at?: string | null;
}

export type ApiNodeRole = "workstation" | "laptop" | "media" | "server" | "other";
export type ApiNodePlatform = "macos" | "linux" | "windows";

/** A room as a node carries it (Phase 13 D2). */
export interface ApiRoomRef {
  id: string;
  name: string;
  key: string;
}

/** `GET/POST /api/v1/workspaces/<id>/rooms` row (Phase 13 D2). */
export interface ApiRoom extends ApiRoomRef {
  aliases: string[];
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface ApiRoomList {
  workspace: string;
  limits: { aliases: number; alias_min_chars: number; alias_max_chars: number; name_max_chars: number };
  results: ApiRoom[];
}

/** One row of `GET /api/v1/nodes/<id>/audit` (Phase 13 D8): a chain row or an event. */
export type ApiNodeAuditRow =
  | ({ kind: "invocation" } & ApiNodeInvocation)
  | {
      kind: "event";
      id: string;
      created_at: string;
      action: string;
      actor_type: string;
      details: Record<string, string | number | boolean | null | string[]>;
    };

export interface ApiNodeAuditPage {
  results: ApiNodeAuditRow[];
  next_cursor: string | null;
}

/** One snapshot entry: availability is the Mac's own (read-only here). */
export interface ApiNodeCapability {
  name: string;
  version: number | string | null;
  class: string | null;
  availability: string | null;
  invocable: boolean;
  reason: string | null;
}

export interface ApiNodeDetail extends ApiNodeSummary {
  capabilities: ApiNodeCapability[];
}

/** One chain row (D8): hashes, codes and sizes -- never args or output. */
export interface ApiNodeInvocation {
  id: string;
  seq: number;
  created_at: string;
  capability: string;
  capability_version: number | string | null;
  class: string;
  status: string;
  refusal_code: string;
  args_sha256: string;
  output_sha256: string;
  output_bytes: number;
  truncated: boolean;
  dropped_unmapped: number;
  duration_ms: number;
  channel: string;
  trace_id: string;
  workspace_id: string;
  workspace_name: string;
  row_hash: string;
}

export interface ApiNodeInvocationPage {
  results: ApiNodeInvocation[];
  next_cursor: number | null;
}

/** `GET/POST/DELETE /api/v1/auth/nodes/kill-switch` (Phase 9 D1). */
export interface ApiNodeKillSwitch {
  engaged: boolean;
  engaged_at: string | null;
  engaged_via: string;
  released_at: string | null;
}

/** One wake-model evaluation row (Phase 11 D11): numbers only. */
export interface ApiVoiceWakeEvaluation {
  id: string;
  kind: "household" | "negative_set" | "positives";
  hours: string;
  false_accepts: number;
  far_per_hour: string | null;
  positives: number;
  false_rejects: number;
  frr: string | null;
  threshold: string;
  refractory_ms: number;
  recorded_at: string;
}

export type ApiVoiceLiveReason =
  | "disabled"
  | "no_household_eval"
  | "far_above_budget"
  | "no_positive_eval"
  | "frr_above_budget"
  | "model_mismatch"
  | "threshold_mismatch"
  | "storm_muted";

/** `GET /api/v1/auth/nodes/voice-live` (Phase 11 D12/D15). */
export interface ApiVoiceLive {
  always_on_enabled: boolean;
  max_far_per_hour: string;
  max_frr: string;
  min_household_hours: string;
  storm_max_stt_per_minute: number;
  any_model_permitted: boolean;
  nodes: {
    device_id: string;
    name: string;
    mute: { engaged_at: string; reason: string } | null;
  }[];
  models: {
    model_sha256: string;
    threshold: string | null;
    refractory_ms: number | null;
    permitted: boolean;
    reasons: ApiVoiceLiveReason[];
    household: ApiVoiceWakeEvaluation | null;
    positives: ApiVoiceWakeEvaluation | null;
    negative_set: ApiVoiceWakeEvaluation | null;
  }[];
}

export interface ApiRemoteKeyState {
  active: boolean;
  created_at: string | null;
  last_used_at: string | null;
  key?: string;
}

// Which channel a turn arrived over (backend Message.Channel, MVP 9 §1).
// `openai`: the OpenAI-compatible facade (Phase 4 branch 6), e.g. Open WebUI.
// `node_voice` (Phase 11 D4): a live voice turn from the Mac's voice child.
export type MessageChannel = "web" | "cli" | "telegram" | "openai" | "node_voice";

// backend Message.InputMode (Phase 6 D9).
export type MessageInputMode = "text" | "voice";

// `POST /api/voice/transcriptions` (Phase 6 D1).
export interface ApiTranscription {
  text: string;
  audio_seconds: number;
  language: string;
}

export type MessageIntent =
  | ""
  | "chat"
  | "memory_query"
  | "knowledge_query"
  | "task_draft"
  | "tool_request"
  // Phase 5 branch 4: one connector read this turn.
  | "connector_read";

// Bookkeeping of one connector read supplied as context for a reply
// (Phase 5 branch 4) -- never the content itself.
export interface ApiConnectorRead {
  tool: string;
  source: string;
  data_class: "firm" | "personal";
  size: number;
  truncated: boolean;
}

export interface ApiConversation {
  id: string;
  workspace: string;
  title: string;
  created_by: string | null;
  created_at: string;
  // One-way (false -> true only): a private conversation feeds no memory.
  private: boolean;
  // One-way (Phase 5 branch 4): personal-class connector data entered this
  // conversation, so it only ever runs on providers allowed personal data.
  personal_data?: boolean;
  // The first message's channel (Phase 4 branch 4 channel badge); null
  // until the conversation has a message.
  origin_channel: MessageChannel | null;
  // Phase 11 D5: the channel that opened the conversation ("" for rows
  // predating it); `node_voice` marks a live Mac voice conversation, so it
  // is badged even while still empty. Optional for older backends.
  opened_via?: MessageChannel | "";
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
  // How the user message's text was produced (Phase 6 D9): `voice` for a
  // transcript. Provenance for the mic badge only, never trust. Optional:
  // rows from before the field existed read as `text`.
  input_mode?: MessageInputMode;
  // Connector reads supplied as context for this reply (Phase 5 branch 4).
  connector_reads?: ApiConnectorRead[];
  // Phase 12 D11: on an async read's follow-up reply, the user message whose
  // turn started the read; null otherwise. Optional for older payloads.
  followup_of?: string | null;
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
      connector_reads?: ApiConnectorRead[];
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
  // ADR-009: set while the row is in the trash ("Çöp kutusu").
  deleted_at?: string | null;
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
  // ADR-009: set while the row is in the trash ("Çöp kutusu").
  deleted_at?: string | null;
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
  // Phase 8B branches 7 and 9 (D12): owner-edited notes saved before overwrite.
  conflicts_saved?: number;
  conflicts_private?: number;
  conflicts_folders?: string[];
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
  skills?: ApiAttachedSkill[];
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

// purge_trash (ADR-009, gate-class permanent delete of trashed rows).
export interface ApiPurgeTrashDisplaySummary {
  kind: "purge_trash";
  task_count: number;
  document_count: number;
  // First few titles of the named rows (truncated server-side).
  titles: string[];
}

// node_action (Phase 10 D6): the whitelisted summary of a gated Mac action.
export interface ApiNodeActionDisplaySummary {
  kind: "node_action";
  title: string;
  capability: string;
  node_name: string;
  repo_name: string;
  trust_verdict: string;
  trust_reason: string;
  voice: boolean;
  voice_label?: string;
  note: string;
  expires_in_seconds: number;
  command_kind?: string;
  recipe?: string;
  bundle_id?: string;
  role?: string;
  element_title?: string;
  text?: string;
  submit?: boolean;
}

export interface ApiNodeActionApprovalExecution {
  status: string;
  refusal_code: string;
  exit_code: number | null;
  confirm_state: string;
  finished_at: string | null;
}

// `POST/GET /api/v1/nodes/<id>/actions[/<execution_id>]` (Phase 10 D8).
export interface ApiNodeActionExecution {
  id: string;
  node_id: string;
  capability: string;
  capability_class: string;
  status: string;
  refusal_code: string;
  confirm_state: string;
  exit_code: number | null;
  output_bytes: number;
  truncated: boolean;
  approval_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type ApiDisplaySummary =
  | ApiNodeActionDisplaySummary
  | ApiHarnessImplementDisplaySummary
  | ApiHarnessPushDisplaySummary
  | ApiPurgeTrashDisplaySummary
  | ApiDeployDisplaySummary
  | ApiConnectorWriteDisplaySummary
  | null;

export type ApprovalOrigin = "human" | "agent" | "harness";

export type ApprovalRequestedChannel = "web" | "cli" | "telegram" | "system" | "unknown";

export interface ApiApprovalRequest {
  id: string;
  workspace: string;
  tool_name: string;
  status: ApprovalStatus;
  // Optional: absent on responses from a backend predating these fields.
  origin?: ApprovalOrigin;
  requested_channel?: ApprovalRequestedChannel;
  requested_by: string | null;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
  // When a pending request stops being decidable (null once decided).
  expires_at?: string | null;
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
    | ApiDeployExecution
    | ApiConnectorWriteExecution
    | ApiNodeActionApprovalExecution
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

// Which surface a token reaches (Phase 4 branch 6): `cli` the Friday CLI
// endpoints, `openai` only the OpenAI-compatible facade (/v1), `hook` only
// the Claude Code hook-event ingest (Phase 8A D6; owner's personal workspace
// only). Fixed at mint.
export type PatKind = "cli" | "openai" | "hook";

export interface ApiPersonalAccessToken {
  id: string;
  name: string;
  kind: PatKind;
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

// Why a harness job row got its agent (Phase 5 D11, HARNESS_AGENT_POLICY);
// "" on rows that predate the field.
export type HarnessAgentRoutingReason =
  | ""
  | "policy:default"
  | "owner_selected"
  | "policy:cross_vendor";

export interface ApiHarnessJob {
  id: string;
  workspace: string;
  repo: string;
  goal: string;
  agent: HarnessAgent;
  agent_routing_reason: HarnessAgentRoutingReason;
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
  // Phase 4 branch 5, read-only: how the ADR-004 self-modification guard
  // sees this repo (`self` = Friday's own remote, implementation refused;
  // `unconfigured` = HARNESS_SELF_REPO_IDENTIFIERS empty, all refused).
  self_repo_status?: ApiRepoSelfStatus;
  created_at: string;
  updated_at: string;
}

export type ApiRepoSelfStatus = "self" | "other" | "unconfigured";

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
  | "secret_leak"
  // Phase 12 D17: an attached skill version became unavailable before the
  // run, or the produced commit touched the mounted skills' prefix.
  | "skill_version_unavailable"
  | "skill_path_in_commit";

// Phase 12 D17: an instruction skill attached to an implementation job, as
// Gate A snapshotted it (name + content hash, never the text).
export interface ApiAttachedSkill {
  id?: string;
  name: string;
  version: number | null;
  sha256: string;
}

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
  agent_routing_reason: HarnessAgentRoutingReason;
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
  skills?: ApiAttachedSkill[];
}

export interface ApiImplementationReview {
  id: string;
  agent: "codex_cli";
  agent_routing_reason: HarnessAgentRoutingReason;
  status: "pending" | "running" | "succeeded" | "failed";
  verdict: "" | "approved" | "changes_requested";
  // Gateway-produced -- Friday's own summarization, trusted-ish.
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

// --- Connectors (Phase 5 branches 4-9) ---

export type ConnectorKind = "mcp" | "native";
export type ConnectorDataClass = "firm" | "personal";
// Pin check per tool (connectors.services): `unchecked` until the first
// check after enabling; `mismatch`/`missing` disable the tool until a
// reviewed manifest commit.
export type ConnectorPinStatus = "ok" | "mismatch" | "missing" | "unchecked";

export interface ApiConnectorTool {
  name: string;
  kind: "read" | "write";
  // Friday-authored (manifest) -- never the server's own description.
  description: string;
  pin_status: string;
}

export interface ApiConnectorBinding {
  connector: string;
  display_name: string;
  kind: ConnectorKind;
  status: "active" | "blocked";
  data_class: ConnectorDataClass;
  manifest_version: number;
  bindable: boolean;
  workspace: string;
  enabled: boolean;
  tools: ApiConnectorTool[];
  health: {
    checked_at: string | null;
    error: string;
    server_name: string;
    server_version: string;
    protocol_version: string;
  };
  updated_at: string | null;
}

// One `connector.read` audit row, bookkeeping only (never args/content).
export interface ApiConnectorRecentRead {
  id: string;
  created_at: string;
  connector: string;
  tool: string;
  outcome: string;
  channel: string;
  data_class: string;
  size: number;
  duration_ms: number | null;
  truncated: boolean;
}

export type ConnectorWriteStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "unknown_outcome";

// connectors.writes.describe_write_execution
export interface ApiConnectorWriteExecution {
  status: ConnectorWriteStatus;
  error_type: string;
  started_at: string | null;
  finished_at: string | null;
}

// connectors.writes.describe_write_request: built from the snapshot only.
// `rows` are the tool renderer's rows (or every argument in full);
// `arguments_json` is the exact canonical JSON that will be sent.
export interface ApiConnectorWriteDisplaySummary {
  kind: "connector_write";
  connector: string;
  connector_name: string;
  tool: string;
  tool_description: string;
  data_class: string;
  manifest_version: number | null;
  rows: { label: string; value: string }[];
  context_rows: { label: string; value: string }[];
  arguments_json: string;
  arguments_sha256: string;
}

// --- Deploys (Phase 5 branch 6) ---

export type DeployRunStatus =
  | "pending"
  | "checking"
  | "deploying"
  | "health_checking"
  | "rolling_back"
  | "succeeded"
  | "succeeded_degraded"
  | "rolled_back"
  | "rollback_failed"
  | "health_failed"
  | "unknown_outcome"
  | "refused"
  | "failed"
  | "cancelled";

export type CiConclusion = "success" | "failure" | "pending" | "unknown";

export interface ApiDeployTarget {
  id: string;
  workspace: string;
  name: string;
  environment: string;
  ssh_host: string;
  ssh_port: number;
  ssh_user: string;
  ssh_destination: string;
  host_public_key: string;
  remote_app: string;
  repo_git_url: string;
  tracked_branch: string;
  health_url: string;
  health_timeout_seconds: number;
  require_ci_success: boolean;
  auto_rollback: boolean;
  enabled: boolean;
  current_sha: string;
  last_status_at: string | null;
  last_status_error: string;
  config_hash: string;
  active_run: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiDeployRun {
  id: string;
  workspace: string;
  target: string;
  target_name: string;
  environment: string;
  approval_request: string;
  sha: string;
  expected_current_sha: string;
  rollback_sha: string;
  status: DeployRunStatus;
  error_code: string;
  needs_attention: boolean;
  deploy_started_at: string | null;
  rollback_started_at: string | null;
  finished_at: string | null;
  health_checks_total: number;
  created_at: string;
  updated_at: string;
}

export interface ApiDeployRunDetail extends ApiDeployRun {
  health_checks: unknown[];
  auto_rollback: boolean;
  health_plan: {
    window_seconds: number | null;
    checks: number | null;
    interval_seconds: number | null;
    pass_threshold: number | null;
    fail_threshold: number | null;
    health_url: string | null;
  };
  // Bounded, redacted remote output: low-trust text, shown as data only.
  output: string;
  output_trust: "low";
}

// GET /deploys/targets/<id>/head -- the "Deploy" dialog prefill.
export interface ApiDeployBranchHead {
  tracked_branch: string;
  current_sha: string;
  sha: string;
  title: string;
  title_trust: "repo_text";
  ci_conclusion: CiConclusion;
  source: "github" | "manual";
  require_ci_success: boolean;
}

// deploys.tool.describe_deploy_request: the whole decided plan.
export interface ApiDeployDisplaySummary {
  kind: "deploy";
  text: string;
  target_id: string;
  target_name: string;
  environment: string;
  ssh_destination: string;
  ssh_port: number;
  remote_app: string;
  repo_git_url: string;
  tracked_branch: string;
  sha: string;
  current_sha: string;
  rollback_sha: string;
  auto_rollback: boolean;
  // Repo text (low-trust), labelled as such.
  commit_title: string;
  commit_title_trust: "repo_text";
  ci_conclusion: string;
  commit_source: string;
  require_ci_success: boolean;
  health: {
    window_seconds: number | null;
    checks: number | null;
    interval_seconds: number | null;
    pass_threshold: number | null;
    fail_threshold: number | null;
    health_url: string;
  };
  verbs: string[];
}

// deploys.tool.describe_deploy_execution (closed vocabulary only).
export interface ApiDeployExecution {
  run_id: string;
  status: DeployRunStatus;
  error_code: string;
  active: boolean;
  needs_attention: boolean;
  deploy_started: boolean;
  rollback_attempted: boolean;
  health_checks: number;
  finished_at: string | null;
}

// --- Harness PR panel (Phase 5 branch 5) ---

export interface ApiCiRun {
  name: string;
  status: string;
  conclusion: string;
  url: string;
}

export interface ApiPullRequestState {
  job_status: ImplStatus;
  head: string;
  base: string;
  head_sha: string;
  can_request: boolean;
  request: { id: string; status: ApprovalStatus; created_at: string } | null;
  execution: { status: ConnectorWriteStatus; error_type: string; finished_at: string | null } | null;
  pull_request: { number: number | null; url: string } | null;
  ci: { state: CiConclusion; runs: ApiCiRun[]; error?: string } | null;
}

// --- Phase 8A learning memory (branch 6 UI) ---

export type CoreBlockLabel = "self" | "developer" | "current_focus" | "active_projects" | "preferences";
export type CoreBlockWriterKind = "owner" | "assembler" | "approved_proposal";

export interface ApiCoreBlock {
  label: CoreBlockLabel;
  content: string;
  version: number;
  sensitivity: MemorySensitivity;
  updated_by_kind: CoreBlockWriterKind | null;
  updated_at: string | null;
  char_budget: number;
  editable: boolean;
}

export interface ApiCoreBlockVersion {
  version: number;
  content: string;
  sensitivity: MemorySensitivity;
  updated_by_kind: CoreBlockWriterKind;
  created_at: string;
}

export type SelfAnchorGroup = "identity" | "rule" | "adr" | "phase";

export interface ApiSelfAnchor {
  group: SelfAnchorGroup;
  label: string;
  file: string;
  line: number | null;
  text: string;
}

export interface ApiSelfSources {
  content_hash: string;
  anchors: ApiSelfAnchor[];
}

export type IngestionSourceKind =
  | "git"
  | "harness"
  | "claude_code_hooks"
  | "calendar"
  | "obsidian_inbox"
  | "claude_code_transcripts"
  | "codex_transcripts";
export type MappingRuleType = "repo" | "path_prefix" | "calendar_binding" | "vault_prefix";

export interface ApiIngestionCounter {
  day: string;
  stored: number;
  duplicate: number;
  dropped_unmapped: number;
  dropped_paused: number;
  /** Phase 8B branch 8: transcript turns refused before storage. */
  dropped_not_owner?: number;
  dropped_foreign?: number;
  scrubber_hits: Record<string, number>;
  tokens_spent: number;
  proposals_created: number;
  proposals_accepted: number;
  proposals_rejected: number;
}

export interface ApiIngestionSource {
  id: string;
  workspace: string;
  kind: IngestionSourceKind;
  enabled: boolean;
  paused: boolean;
  paused_at: string | null;
  daily_token_budget: number;
  forgotten_at: string | null;
  restorable: boolean;
  /** Phase 8B branch 8: when the owner last enabled the source. */
  enabled_at?: string | null;
  /** Phase 8B branch 8 (D13): the source whose path rules this one uses. */
  rules_shared_with?: IngestionSourceKind | null;
  today: ApiIngestionCounter | null;
}

export interface ApiEpisode {
  id: string;
  workspace: string;
  workspace_name: string;
  kind: string;
  occurred_at: string;
  owner_text: string;
  context_text: string;
  metadata: Record<string, unknown>;
  sensitivity: MemorySensitivity;
  scrub_report: Record<string, number>;
  token_estimate: number;
  expires_at: string;
  extracted: boolean;
}

export interface ApiForgetPreview {
  episodes: number;
  memories: number;
  core_block_proposals: number;
}

export interface ApiIngestionAudit extends ApiIngestionSource {
  counters: ApiIngestionCounter[];
  episodes: ApiEpisode[];
  scrubber_hits: Record<string, number>;
  unmapped_keys: { hash: string; short: string }[];
  tokens_spent_today: number;
  acceptance: { created: number; accepted: number; rejected: number; rate: number | null };
  forget_preview: ApiForgetPreview;
  /** Phase 8B branch 9 (D13): one entry per cursor of a transcript source. */
  transcript_cursors?: ApiTranscriptCursor[];
}

export interface ApiTranscriptCursor {
  target_workspace: string;
  target_workspace_name: string;
  node_ref: string;
  since: string;
  since_clamped: number;
  last_clamped_at: string | null;
  /** The window the 7-day contract bound skipped (lost by design). */
  last_skipped_window: { from: string; to: string } | null;
  tie_overflow: number;
}

export interface ApiForgetResult {
  operation_id: string;
  episodes: number;
  memories_archived?: number;
  memories_rejected?: number;
  memories?: number;
  core_block_proposals: number;
  candidates_discarded?: number;
  source: ApiIngestionSource;
}

export interface ApiMappingRule {
  id: string;
  source: string;
  rule_type: MappingRuleType;
  pattern: string;
  target_workspace: string;
  enabled: boolean;
  created_at: string;
}

export interface ApiScrubTerm {
  id: string;
  workspace: string;
  term: string;
}

export type CodingSessionStatus = "running" | "waiting_for_owner" | "stopped" | "stale";

export interface ApiCodingSession {
  id: string;
  workspace: { id: string; name: string };
  tool: string;
  session_id: string;
  project: string;
  status: CodingSessionStatus;
  stored_status: Exclude<CodingSessionStatus, "stale">;
  started_at: string;
  last_event_at: string;
  last_event: string;
  last_notification: string;
  event_count: number;
}

export type LearningItemKind = "memory" | "core-block";

interface ApiLearningItemBase {
  id: string;
  title: string;
  content: string;
  quote: string;
  source_kind: IngestionSourceKind | null;
  created_at: string;
  bulk_approvable: boolean;
}

export interface ApiLearningMemoryItem extends ApiLearningItemBase {
  kind: "memory";
  memory_type: MemoryType;
  sensitivity: MemorySensitivity;
  actions: ("approve" | "edit" | "reject")[];
}

export interface ApiLearningCoreBlockItem extends ApiLearningItemBase {
  kind: "core-block";
  label: CoreBlockLabel;
  diff: string;
  base_version: number;
  inferred: boolean;
  actions: ("approve" | "reject")[];
}

/** The learned predicate vocabulary v1 (Phase 8B D2; `MemoryFact.Predicate`, learned set). */
export type LearnedFactPredicate =
  | "decided"
  | "uses"
  | "depends_on"
  | "deployed_to"
  | "owned_by"
  | "works_on"
  | "prefers"
  | "blocked_by"
  | "replaced_by"
  | "has_status";

export interface ApiFactEntityRef {
  kind: string;
  name: string;
}

/** A learned fact proposal on the morning card (Phase 8B D4). */
export interface ApiLearningFactItem extends ApiLearningItemBase {
  kind: "fact";
  statement: string;
  predicate: LearnedFactPredicate;
  subject: ApiFactEntityRef;
  object: ApiFactEntityRef | { text: string };
  valid_from: string;
  valid_to: string | null;
  confidence: number;
  sensitivity: MemorySensitivity;
  /** The current fact this proposal would replace if approved; `changed` once it no longer is current. */
  supersedes: { id: string; statement: string; valid_from: string; changed: boolean } | null;
  actions: ("approve" | "edit" | "reject")[];
}

export type ApiLearningItem = ApiLearningMemoryItem | ApiLearningCoreBlockItem | ApiLearningFactItem;

export interface ApiLearningDigest {
  since: string;
  count: number;
  workspaces: {
    workspace: { id: string; name: string; type: "personal" | "company" };
    items: ApiLearningItem[];
  }[];
}

// --- The temporal graph (Phase 8B D2, branch 9: /memory/graph) ---

export type GraphEntityKind =
  | "repo"
  | "service"
  | "firm"
  | "person_role"
  | "project"
  | "tool"
  | "concept"
  | "code_module"
  | "code_class"
  | "code_function";

/** The kinds an owner (or a learned proposal) may name; `code_*` come only from Graphify. */
export type LearnedEntityKind = Exclude<GraphEntityKind, "code_module" | "code_class" | "code_function">;

export type GraphFactPredicate = LearnedFactPredicate | "contains" | "imports" | "calls" | "inherits";
export type GraphFactStatus = "pending_approval" | "approved" | "rejected" | "archived" | "system";
export type GraphOrigin = "learned" | "graphify";

export interface ApiGraphEntityRef {
  id: string;
  kind: GraphEntityKind;
  name: string;
}

export interface ApiGraphEntity {
  id: string;
  workspace: string;
  kind: GraphEntityKind;
  name: string;
  aliases: string[];
  origin: GraphOrigin;
  is_private: boolean | null;
  created_at: string;
  updated_at: string;
}

export interface ApiGraphFact {
  id: string;
  workspace: string;
  subject: ApiGraphEntityRef;
  predicate: GraphFactPredicate;
  object_entity: ApiGraphEntityRef | null;
  object_text: string;
  statement: string;
  valid_from: string;
  valid_to: string | null;
  superseded_by: string | null;
  supersedes: string | null;
  confidence: number;
  status: GraphFactStatus;
  origin: GraphOrigin;
  sensitivity: MemorySensitivity;
  provenance_quote: string;
  source_episode_id: string | null;
  ingestion_source: string | null;
  in_trash: boolean;
  created_at: string;
  updated_at: string;
  hop?: number;
}

/** A Graphify snapshot (`CodeGraphImport`): sha, times and counts only. */
export interface ApiCodeGraphSnapshot {
  commit_sha: string;
  committed_at: string | null;
  imported_at: string | null;
  entity_count: number;
  fact_count: number;
  retired_at: string | null;
}

export interface ApiGraphEntityDetail {
  entity: ApiGraphEntity;
  edges: ApiGraphFact[];
  /** The live learned facts an owner trash moves with the entity. */
  fact_count: number;
  code_graph: ApiCodeGraphSnapshot | null;
}

export type GraphifyRunStatus = "pending" | "running" | "succeeded" | "failed";

export interface ApiGraphifyRun {
  id: string;
  workspace: string;
  repo: { id: string; name: string };
  status: GraphifyRunStatus;
  requested_sha: string;
  commit_sha: string;
  committed_at: string | null;
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
  error_type: string;
  counts: Record<string, number>;
  created_at: string;
}

// --- Eval runs (Phase 8B D9): metrics only, never case text ---

export type EvalSet = "public" | "private";
export type EvalKind = "run" | "baseline" | "compare";
export type EvalVerdict = "pass" | "fail" | "inconclusive";

export interface ApiEvalRun {
  id: string;
  run_id: string;
  set: EvalSet;
  kind: EvalKind;
  retrieval_mode: string;
  compression_purpose: string | null;
  arm: "a" | "b" | null;
  started_at: string;
  finished_at: string;
  total: number;
  answered: number;
  no_provider: number;
  budget_stopped: number;
  /** A decimal as text (`Decimal` serialised by the backend). */
  spend_usd: string;
  recall_at_10: number | null;
  leak_rate: number | null;
  temporal_correctness: number | null;
  answer_accuracy: number | null;
  pre_tokens: number | null;
  post_tokens: number | null;
  verdict: EvalVerdict | null;
}
