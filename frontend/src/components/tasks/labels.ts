import type { TaskPriority, TaskStatus } from "@/types/api";

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  open: "Açık",
  done: "Tamamlandı",
  cancelled: "İptal edildi",
};

export const TASK_STATUSES: TaskStatus[] = ["open", "done", "cancelled"];

export const TASK_PRIORITY_LABELS: Record<TaskPriority, string> = {
  low: "Düşük",
  normal: "Normal",
  high: "Yüksek",
};

export const TASK_PRIORITIES: TaskPriority[] = ["low", "normal", "high"];
