import { tr } from "@/lib/i18n/tr";
import type { KnowledgeSensitivity, KnowledgeSourceType, KnowledgeStatus } from "@/types/api";

// Strings live in the Turkish catalog (lib/i18n/tr.ts); re-exported here
// under their established names.
export const KNOWLEDGE_SOURCE_TYPE_LABELS: Record<KnowledgeSourceType, string> =
  tr.knowledge.sourceType;

export const KNOWLEDGE_SOURCE_TYPES: KnowledgeSourceType[] = ["pasted_text", "file_upload", "url"];

export const KNOWLEDGE_SENSITIVITY_LABELS: Record<KnowledgeSensitivity, string> =
  tr.knowledge.sensitivity;

export const KNOWLEDGE_STATUS_LABELS: Record<KnowledgeStatus, string> = tr.status.knowledge;

export const KNOWLEDGE_STATUSES: KnowledgeStatus[] = ["pending", "processing", "ready", "failed"];
