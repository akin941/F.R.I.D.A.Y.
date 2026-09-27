"use client";

import { useLayoutEffect, useRef } from "react";
import { ArrowUp, Square } from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { tr } from "@/lib/i18n/tr";

/** Tallest the composer grows before its own content scrolls. */
export const COMPOSER_MAX_HEIGHT_PX = 192;

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled: boolean;
}

/**
 * Enter sends, Shift+Enter inserts a newline. An Enter that confirms an IME
 * composition (Japanese, Chinese, Korean, ... -- `isComposing`, or the
 * legacy keyCode 229 some browsers report instead) must never send a
 * half-composed message.
 */
export function shouldSendOnKeyDown(event: {
  key: string;
  shiftKey: boolean;
  nativeEvent: { isComposing?: boolean };
  keyCode?: number;
}): boolean {
  if (event.key !== "Enter" || event.shiftKey) return false;
  if (event.nativeEvent.isComposing || event.keyCode === 229) return false;
  return true;
}

/**
 * The message composer: sticky at the bottom of the chat column with the
 * safe-area inset (home indicator) as bottom padding, and a textarea that
 * grows with its content up to `COMPOSER_MAX_HEIGHT_PX`. The textarea
 * stays editable while a reply streams (disabling a focused field would
 * close a phone's keyboard mid-thought); only sending is blocked.
 */
export function Composer({ value, onChange, onSend, onStop, isStreaming, disabled }: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Autosize for browsers without `field-sizing: content`: collapse, then
  // grow to the content height, capped.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, COMPOSER_MAX_HEIGHT_PX);
    if (next > 0) el.style.height = `${next}px`;
  }, [value]);

  const canSend = !isStreaming && !disabled && value.trim().length > 0;

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (!shouldSendOnKeyDown(event)) return;
    event.preventDefault();
    if (canSend) onSend();
  }

  return (
    <div className="sticky bottom-0 z-10 border-t border-border bg-background px-3 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
      <div className="mx-auto flex w-full max-w-3xl items-end gap-2">
        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={tr.chat.placeholder}
          aria-label={tr.chat.composerLabel}
          disabled={disabled}
          rows={1}
          enterKeyHint="send"
          className="min-h-10 flex-1 resize-none"
          style={{ maxHeight: COMPOSER_MAX_HEIGHT_PX }}
        />
        {isStreaming ? (
          <Button type="button" variant="outline" size="lg" onClick={onStop}>
            <Square aria-hidden className="fill-current" />
            <span className="sr-only sm:not-sr-only">{tr.chat.stop}</span>
          </Button>
        ) : (
          <Button type="button" size="lg" onClick={onSend} disabled={!canSend}>
            <ArrowUp aria-hidden />
            <span className="sr-only sm:not-sr-only">{tr.chat.send}</span>
          </Button>
        )}
      </div>
    </div>
  );
}
