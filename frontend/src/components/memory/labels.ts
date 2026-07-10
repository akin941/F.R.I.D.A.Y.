import type { MemorySensitivity, MemoryStatus, MemoryType } from "@/types/api";

export const MEMORY_TYPE_LABELS: Record<MemoryType, string> = {
  semantic: "Semantic",
  episodic: "Episodic",
  preference: "Preference",
  procedural: "Procedural",
};

export const MEMORY_TYPES: MemoryType[] = ["semantic", "episodic", "preference", "procedural"];

export const MEMORY_SENSITIVITY_LABELS: Record<MemorySensitivity, string> = {
  normal: "Normal",
  private: "Private",
};

export const MEMORY_STATUS_LABELS: Record<MemoryStatus, string> = {
  pending_approval: "Pending approval",
  approved: "Approved",
  rejected: "Rejected",
  archived: "Archived",
};
