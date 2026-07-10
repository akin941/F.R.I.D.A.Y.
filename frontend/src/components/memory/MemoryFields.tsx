"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { MEMORY_SENSITIVITY_LABELS, MEMORY_TYPE_LABELS, MEMORY_TYPES } from "@/components/memory/labels";
import type { MemorySensitivity, MemoryType } from "@/types/api";

export interface MemoryFieldsValue {
  title: string;
  content: string;
  memory_type: MemoryType;
  sensitivity: MemorySensitivity;
}

export function MemoryFields({
  value,
  onChange,
  idPrefix,
}: {
  value: MemoryFieldsValue;
  onChange: (value: MemoryFieldsValue) => void;
  idPrefix: string;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${idPrefix}-title`}>Title</Label>
        <Input
          id={`${idPrefix}-title`}
          value={value.title}
          onChange={(e) => onChange({ ...value, title: e.target.value })}
          required
        />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${idPrefix}-content`}>Content</Label>
        <Textarea
          id={`${idPrefix}-content`}
          value={value.content}
          onChange={(e) => onChange({ ...value, content: e.target.value })}
          required
        />
      </div>
      <div className="flex gap-3">
        <div className="flex flex-1 flex-col gap-2">
          <Label htmlFor={`${idPrefix}-type`}>Type</Label>
          <Select
            value={value.memory_type}
            onValueChange={(memory_type) =>
              onChange({ ...value, memory_type: memory_type as MemoryType })
            }
          >
            <SelectTrigger id={`${idPrefix}-type`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {MEMORY_TYPES.map((type) => (
                <SelectItem key={type} value={type}>
                  {MEMORY_TYPE_LABELS[type]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-1 flex-col gap-2">
          <Label htmlFor={`${idPrefix}-sensitivity`}>Sensitivity</Label>
          <Select
            value={value.sensitivity}
            onValueChange={(sensitivity) =>
              onChange({ ...value, sensitivity: sensitivity as MemorySensitivity })
            }
          >
            <SelectTrigger id={`${idPrefix}-sensitivity`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="normal">{MEMORY_SENSITIVITY_LABELS.normal}</SelectItem>
              <SelectItem value="private">{MEMORY_SENSITIVITY_LABELS.private}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
    </div>
  );
}
