import type { MemorySensitivity, MemoryStatus, MemoryType } from "@/types/api";

export const MEMORY_TYPE_LABELS: Record<MemoryType, string> = {
  semantic: "Anlamsal",
  episodic: "Anısal",
  preference: "Tercih",
  procedural: "Yordamsal",
  // System-generated activity summaries (ADR-008 k.4, Phase 1
  // p1-work-log) — never user-creatable, so deliberately excluded from
  // MEMORY_TYPES (the create/edit form's options) below; only shown where
  // an existing work_log memory is rendered or filtered.
  work_log: "Sistem günlüğü",
};

export const MEMORY_TYPES: MemoryType[] = ["semantic", "episodic", "preference", "procedural"];

// Includes every type a memory list/filter might actually need to display,
// unlike MEMORY_TYPES above (the create/edit form's selectable options,
// which must never offer work_log — the backend rejects creating or
// retyping into it outside record_work_log()'s system-generated path).
export const MEMORY_FILTER_TYPES: MemoryType[] = [...MEMORY_TYPES, "work_log"];

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
