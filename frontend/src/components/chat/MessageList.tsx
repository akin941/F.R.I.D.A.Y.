"use client";

import { Fragment, useRef } from "react";
import { Button } from "@/components/ui/button";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { useStickToBottom } from "@/components/chat/scroll";
import type { ChatMessageView } from "@/components/chat/types";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";

/**
 * A signature that changes only when rendered content changes: message
 * count, total text length and the extras present. Ids are deliberately
 * left out -- a local bubble replaced by its identical persisted row must
 * not count as "new content" (it would raise the pill for nothing).
 */
function contentSignature(messages: ChatMessageView[], extrasKey: string): string {
  let length = 0;
  for (const m of messages) length += m.content.length;
  const last = messages.at(-1);
  // The interrupted notice adds content; streaming -> complete does not.
  const interrupted = last?.status === "partial" || last?.status === "failed";
  return `${messages.length}:${length}:${interrupted}:${extrasKey}`;
}

export function MessageList({
  messages,
  workspaceId,
  renderAfter,
  extrasKey = "",
  footer,
  followSignal,
  retryFor,
}: {
  messages: ChatMessageView[];
  workspaceId: string;
  /** Extra content (draft cards) rendered after a given message. */
  renderAfter?: (message: ChatMessageView) => React.ReactNode;
  /** Changes whenever the set of extras changes (drives smart scroll). */
  extrasKey?: string;
  footer?: React.ReactNode;
  /** Bumped when the reader sends: always resume following. */
  followSignal: number;
  /** The retry action for a message, if it gets one. */
  retryFor?: (message: ChatMessageView) => (() => void) | undefined;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const { showPill, scrollToBottom, onScroll } = useStickToBottom(
    scrollRef,
    contentSignature(messages, extrasKey),
    followSignal,
  );

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        data-testid="message-scroll"
        className={cn(VIEW_SCROLL_ROOT, "overscroll-contain")}
      >
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-4 py-6">
          {messages.map((message) => (
            <Fragment key={message.id}>
              <MessageBubble
                message={message}
                workspaceId={workspaceId}
                onRetry={retryFor?.(message)}
              />
              {renderAfter?.(message)}
            </Fragment>
          ))}
          {footer}
        </div>
      </div>
      {showPill ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
          <Button
            size="sm"
            variant="secondary"
            className="pointer-events-auto rounded-full shadow-md"
            onClick={scrollToBottom}
          >
            {tr.chat.newMessages}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
