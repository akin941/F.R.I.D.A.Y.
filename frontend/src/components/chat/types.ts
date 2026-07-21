import type { MessageRole, MessageStatus } from "@/types/api";

export interface ChatMessageView {
  id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  // Memory context supplied to the model for this reply (ids only, no
  // content) — "memory context", not proof the model used it. Empty/absent
  // for user messages and for replies with nothing injected.
  memoriesUsed?: string[];
  // Same contract, for RAG knowledge chunks (MVP 3 §4).
  chunksUsed?: string[];
}
