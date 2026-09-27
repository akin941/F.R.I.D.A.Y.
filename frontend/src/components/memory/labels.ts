import { tr } from "@/lib/i18n/tr";
import type { MemorySensitivity, MemoryStatus, MemoryType } from "@/types/api";

// Strings live in the Turkish catalog (lib/i18n/tr.ts); re-exported here
// under their established names.
export const MEMORY_TYPE_LABELS: Record<MemoryType, string> = tr.memory.type;

// The create/edit form's selectable types: never `work_log` (system-
// generated only; the backend rejects creating or retyping into it).
export const MEMORY_TYPES: MemoryType[] = ["semantic", "episodic", "preference", "procedural"];

// Includes every type a memory list/filter might actually need to display,
// unlike MEMORY_TYPES above.
export const MEMORY_FILTER_TYPES: MemoryType[] = [...MEMORY_TYPES, "work_log"];

export const MEMORY_SENSITIVITY_LABELS: Record<MemorySensitivity, string> = tr.memory.sensitivity;

export const MEMORY_STATUS_LABELS: Record<MemoryStatus, string> = tr.status.memory;
