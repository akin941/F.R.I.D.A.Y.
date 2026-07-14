import type { MemorySensitivity, MemoryStatus, MemoryType } from "@/types/api";

export const MEMORY_TYPE_LABELS: Record<MemoryType, string> = {
  semantic: "Anlamsal",
  episodic: "Anısal",
  preference: "Tercih",
  procedural: "Yordamsal",
};

export const MEMORY_TYPES: MemoryType[] = ["semantic", "episodic", "preference", "procedural"];

export const MEMORY_SENSITIVITY_LABELS: Record<MemorySensitivity, string> = {
  normal: "Normal",
  private: "Özel",
};

export const MEMORY_STATUS_LABELS: Record<MemoryStatus, string> = {
  pending_approval: "Onay bekliyor",
  approved: "Onaylandı",
  rejected: "Reddedildi",
  archived: "Arşivlendi",
};
