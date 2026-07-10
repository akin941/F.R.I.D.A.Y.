import type { KnowledgeSensitivity, KnowledgeSourceType, KnowledgeStatus } from "@/types/api";

export const KNOWLEDGE_SOURCE_TYPE_LABELS: Record<KnowledgeSourceType, string> = {
  pasted_text: "Paste text",
  file_upload: "Upload file",
  url: "From URL",
};

export const KNOWLEDGE_SOURCE_TYPES: KnowledgeSourceType[] = ["pasted_text", "file_upload", "url"];

export const KNOWLEDGE_SENSITIVITY_LABELS: Record<KnowledgeSensitivity, string> = {
  normal: "Normal",
  private: "Private",
};

export const KNOWLEDGE_STATUS_LABELS: Record<KnowledgeStatus, string> = {
  pending: "Pending",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};

export const KNOWLEDGE_STATUSES: KnowledgeStatus[] = ["pending", "processing", "ready", "failed"];
