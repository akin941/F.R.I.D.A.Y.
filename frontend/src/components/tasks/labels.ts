import { tr } from "@/lib/i18n/tr";
import type { TaskPriority, TaskStatus } from "@/types/api";

// Strings live in the Turkish catalog (lib/i18n/tr.ts); re-exported here
// under their established names.
export const TASK_STATUS_LABELS: Record<TaskStatus, string> = tr.status.task;

export const TASK_STATUSES: TaskStatus[] = ["open", "done", "cancelled"];

export const TASK_PRIORITY_LABELS: Record<TaskPriority, string> = tr.task.priority;

export const TASK_PRIORITIES: TaskPriority[] = ["low", "normal", "high"];
