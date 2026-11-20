"use client";

import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CopyButton, Markdown } from "@/components/ui/markdown";
import { ChannelBadge, SourceBadges } from "@/components/chat/badges";
import { SourcesList } from "@/components/chat/SourcesList";
import type { ChatMessageView } from "@/components/chat/types";
import { tr } from "@/lib/i18n/tr";

function TypingDots() {
  return (
    <span aria-hidden className="inline-flex gap-1 py-2" data-testid="typing-indicator">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1.5 animate-pulse rounded-full bg-muted-foreground"
          style={{ animationDelay: `${i * 150}ms` }}
        />
      ))}
    </span>
  );
}

/**
 * One message. User text renders verbatim (pre-wrapped); assistant text
 * renders as safe Markdown -- streaming-safe, since CommonMark runs an
 * unclosed fence to the end of the text, so a half-streamed code block
 * shows as code until its closing fence arrives, with no reflow when the
 * reply completes (the final and the streamed text render identically).
 */
export function MessageBubble({
  message,
  workspaceId,
  onRetry,
}: {
  message: ChatMessageView;
  workspaceId: string;
  /** Offered on the latest reply only, when it ended partial or failed. */
  onRetry?: () => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex flex-col items-end gap-1" data-testid="user-message">
        <div className="max-w-[85%] rounded-2xl bg-primary px-4 py-2 text-sm break-words whitespace-pre-wrap text-primary-foreground">
          {message.content}
        </div>
        <ChannelBadge channel={message.channel} />
      </div>
    );
  }

  const streaming = message.status === "streaming";
  const interrupted = message.status === "partial" || message.status === "failed";
  return (
    <div className="flex flex-col gap-1.5" data-testid="assistant-message">
      {message.content ? (
        <Markdown>{message.content}</Markdown>
      ) : streaming ? (
        <TypingDots />
      ) : null}
      {interrupted ? (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-md border border-warning/30 bg-warning-muted px-2.5 py-1.5 text-xs text-warning"
        >
          <span>{message.status === "failed" ? tr.chat.failed : tr.chat.interrupted}</span>
          {onRetry ? (
            <Button variant="outline" size="xs" onClick={onRetry}>
              <RotateCcw aria-hidden />
              {tr.chat.retry}
            </Button>
          ) : null}
        </div>
      ) : null}
      {/* The action row is laid out while streaming too (its copy button
          hidden), so the reply's height does not change when it completes:
          no layout jump at the end of a stream. */}
      <div className="flex min-h-6 flex-wrap items-start gap-2">
        {message.content ? (
          <span className={streaming ? "invisible" : undefined}>
            <CopyButton
              text={message.content}
              label={tr.chat.copyMessage}
              className="-ml-1 text-muted-foreground"
            />
          </span>
        ) : null}
        <ChannelBadge channel={message.channel} className="mt-0.5" />
        <SourceBadges reads={message.connectorReads} className="mt-0.5" />
        <div className="min-w-0 flex-1 pt-1">
          <SourcesList
            workspaceId={workspaceId}
            memoryIds={message.memoriesUsed ?? []}
            chunkIds={message.chunksUsed ?? []}
            chunkDocuments={message.chunkDocuments ?? {}}
            overlaySourceWorkspaces={message.overlaySourceWorkspaces ?? {}}
          />
        </div>
      </div>
    </div>
  );
}
