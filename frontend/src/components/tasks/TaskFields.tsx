"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { TASK_PRIORITIES, TASK_PRIORITY_LABELS } from "@/components/tasks/labels";
import type { TaskPriority } from "@/types/api";

export interface TaskFieldsValue {
  title: string;
  description: string;
  // "" means no due date; otherwise an ISO YYYY-MM-DD string (matches the
  // native <input type="date"> value format).
  due_date: string;
  priority: TaskPriority;
}

export function TaskFields({
  value,
  onChange,
  idPrefix,
}: {
  value: TaskFieldsValue;
  onChange: (value: TaskFieldsValue) => void;
  idPrefix: string;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${idPrefix}-title`}>Başlık</Label>
        <Input
          id={`${idPrefix}-title`}
          value={value.title}
          onChange={(e) => onChange({ ...value, title: e.target.value })}
          required
        />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${idPrefix}-description`}>Açıklama</Label>
        <Textarea
          id={`${idPrefix}-description`}
          value={value.description}
          onChange={(e) => onChange({ ...value, description: e.target.value })}
        />
      </div>
      <div className="flex gap-3">
        <div className="flex flex-1 flex-col gap-2">
          <Label htmlFor={`${idPrefix}-due-date`}>Son tarih</Label>
          <Input
            id={`${idPrefix}-due-date`}
            type="date"
            value={value.due_date}
            onChange={(e) => onChange({ ...value, due_date: e.target.value })}
          />
        </div>
        <div className="flex flex-1 flex-col gap-2">
          <Label htmlFor={`${idPrefix}-priority`}>Öncelik</Label>
          <Select
            value={value.priority}
            onValueChange={(priority) => onChange({ ...value, priority: priority as TaskPriority })}
          >
            <SelectTrigger id={`${idPrefix}-priority`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TASK_PRIORITIES.map((priority) => (
                <SelectItem key={priority} value={priority}>
                  {TASK_PRIORITY_LABELS[priority]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
    </div>
  );
}
