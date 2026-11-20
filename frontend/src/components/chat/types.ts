import type {
  ApiConnectorRead,
  ApiMessage,
  MessageChannel,
  MessageInputMode,
  MessageIntent,
  MessageRole,
  MessageStatus,
} from "@/types/api";

/**
 * One rendered chat message: a persisted row, or this tab's local bubble
 * for a turn the server has not returned yet (`local`). `streaming` is a
 * view-only status for the assistant bubble while its reply is arriving.
 */
export interface ChatMessageView {
  id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus | "streaming";
  local?: boolean;
  channel?: MessageChannel;
  // Provenance only (Phase 6 D9): a spoken turn shows a mic badge.
  inputMode?: MessageInputMode;
  intent?: MessageIntent;
  intentPayload?: Record<string, unknown>;
  confirmedTask?: string | null;
  // Memory context supplied to the model for this reply (ids only, no
  // content) — "memory context", not proof the model used it. Empty/absent
  // for user messages and for replies with nothing injected.
  memoriesUsed?: string[];
  // Same contract, for RAG knowledge chunks (MVP 3 §4).
  chunksUsed?: string[];
  // {chunk_id: document_id} for the "Kaynaklar" deep links.
  chunkDocuments?: Record<string, string>;
  // {memory_id: source_workspace_id} for overlay-sourced memories.
  overlaySourceWorkspaces?: Record<string, string>;
  // Connector reads supplied as context (Phase 5): bookkeeping only.
  connectorReads?: ApiConnectorRead[];
}

export function toView(message: ApiMessage): ChatMessageView {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    status: message.status,
    channel: message.channel,
    inputMode: message.input_mode,
    intent: message.intent,
    intentPayload: message.intent_payload,
    confirmedTask: message.confirmed_task ?? null,
    memoriesUsed: message.memories_used,
    chunksUsed: message.chunks_used,
    chunkDocuments: message.chunk_documents ?? {},
    overlaySourceWorkspaces: message.overlay_source_workspaces ?? {},
    connectorReads: message.connector_reads ?? [],
  };
}
