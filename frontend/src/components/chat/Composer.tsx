"use client";

import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled: boolean;
}

export function Composer({ value, onChange, onSend, onStop, isStreaming, disabled }: ComposerProps) {
  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!isStreaming && !disabled && value.trim()) {
        onSend();
      }
    }
  }

  return (
    <div className="flex items-end gap-2 border-t border-border p-4">
      <Textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Message Jarvis…"
        disabled={disabled || isStreaming}
        rows={2}
        className="flex-1 resize-none"
      />
      {isStreaming ? (
        <Button type="button" variant="outline" onClick={onStop}>
          Stop
        </Button>
      ) : (
        <Button type="button" onClick={onSend} disabled={disabled || !value.trim()}>
          Send
        </Button>
      )}
    </div>
  );
}
