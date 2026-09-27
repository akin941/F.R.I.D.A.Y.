"use client";

import { Lock, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { channelLabel } from "@/components/chat/badges";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";
import type { ApiConversation } from "@/types/api";

interface ConversationListProps {
  conversations: ApiConversation[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  loading: boolean;
  error?: unknown;
  onRetry?: () => void;
  disabled: boolean;
}

/** The conversation list: the desktop column and the phone drawer's body. */
export function ConversationList({
  conversations,
  selectedId,
  onSelect,
  onNewChat,
  loading,
  error,
  onRetry,
  disabled,
}: ConversationListProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="p-2">
        <Button className="w-full" onClick={onNewChat} disabled={disabled}>
          <Plus aria-hidden />
          {tr.chat.newChat}
        </Button>
      </div>
      <nav aria-label={tr.chat.conversations} className={VIEW_SCROLL_ROOT}>
        <div className="flex flex-col gap-0.5 p-2">
          {loading ? (
            <>
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </>
          ) : error ? (
            <ErrorState error={error} title={tr.chat.loadConversationsFailed} onRetry={onRetry} />
          ) : conversations.length === 0 ? (
            <p className="p-2 text-sm text-muted-foreground">{tr.chat.noConversations}</p>
          ) : (
            conversations.map((conversation) => {
              const selected = selectedId === conversation.id;
              const channel =
                conversation.origin_channel && conversation.origin_channel !== "web"
                  ? conversation.origin_channel
                  : null;
              return (
                <button
                  key={conversation.id}
                  type="button"
                  onClick={() => onSelect(conversation.id)}
                  disabled={disabled}
                  aria-current={selected ? "page" : undefined}
                  className={cn(
                    "flex min-h-9 w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-accent hover:text-accent-foreground disabled:opacity-60",
                    selected && "bg-accent text-accent-foreground",
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {conversation.title || tr.chat.untitled}
                  </span>
                  {channel ? (
                    <span className="shrink-0 rounded border px-1 text-[0.65rem] text-muted-foreground">
                      {channelLabel(channel)}
                    </span>
                  ) : null}
                  {conversation.private ? (
                    <span role="img" aria-label={tr.chat.private} title={tr.chat.privateHint}>
                      <Lock aria-hidden className="size-3.5 shrink-0 text-warning" />
                    </span>
                  ) : null}
                </button>
              );
            })
          )}
        </div>
      </nav>
    </div>
  );
}
