import type { KnowledgeSensitivity, KnowledgeSourceType, KnowledgeStatus } from "@/types/api";

export const KNOWLEDGE_SOURCE_TYPE_LABELS: Record<KnowledgeSourceType, string> = {
  pasted_text: "Metin yapıştır",
  file_upload: "Dosya yükle",
  url: "URL'den",
};

export const KNOWLEDGE_SOURCE_TYPES: KnowledgeSourceType[] = ["pasted_text", "file_upload", "url"];

export const KNOWLEDGE_SENSITIVITY_LABELS: Record<KnowledgeSensitivity, string> = {
  normal: "Normal",
  private: "Özel",
};

export const KNOWLEDGE_STATUS_LABELS: Record<KnowledgeStatus, string> = {
  pending: "Beklemede",
  processing: "İşleniyor",
  ready: "Hazır",
  failed: "Başarısız",
};

export const KNOWLEDGE_STATUSES: KnowledgeStatus[] = ["pending", "processing", "ready", "failed"];
