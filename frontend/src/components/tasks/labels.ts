import type { TaskPriority, TaskStatus } from "@/types/api";

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  open: "Open",
  done: "Done",
  cancelled: "Cancelled",
};

export const TASK_STATUSES: TaskStatus[] = ["open", "done", "cancelled"];

export const TASK_PRIORITY_LABELS: Record<TaskPriority, string> = {
  low: "Low",
  normal: "Normal",
  high: "High",
};

export const TASK_PRIORITIES: TaskPriority[] = ["low", "normal", "high"];
