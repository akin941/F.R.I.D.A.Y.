export interface ApiUser {
  id: string;
  email: string;
  name: string;
  is_agent: boolean;
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

export interface SessionResponse {
  user: ApiUser;
  workspaces: ApiWorkspaceSummary[];
}

export interface ApiConversation {
  id: string;
  workspace: string;
  title: string;
  created_by: string | null;
  created_at: string;
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
}

export type ChatStreamEvent =
  | { type: "chunk"; text: string }
  | {
      type: "done";
      message_id: string;
      usage?: Record<string, unknown>;
      memories_used: string[];
      chunks_used: string[];
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

export type MemoryType = "semantic" | "episodic" | "preference" | "procedural";
export type MemorySensitivity = "normal" | "private";
export type MemoryStatus = "pending_approval" | "approved" | "rejected" | "archived";
export type MemorySource = "ai_suggested" | "user_created";

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
  execution: ApiVaultExportExecution | null;
}
