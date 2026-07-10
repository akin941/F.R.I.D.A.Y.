"use client";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { ApiConversation } from "@/types/api";

interface ConversationListProps {
  conversations: ApiConversation[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  loading: boolean;
  disabled: boolean;
}

export function ConversationList({
  conversations,
  selectedId,
  onSelect,
  onNewChat,
  loading,
  disabled,
}: ConversationListProps) {
  return (
    <div className="flex h-full w-64 shrink-0 flex-col border-r border-border">
      <div className="p-2">
        <Button className="w-full" onClick={onNewChat} disabled={disabled}>
          New chat
        </Button>
      </div>
      <ScrollArea className="flex-1">
        <div className="flex flex-col gap-1 p-2">
          {loading ? (
            <>
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </>
          ) : conversations.length === 0 ? (
            <p className="p-2 text-sm text-muted-foreground">No conversations yet.</p>
          ) : (
            conversations.map((conversation) => (
              <button
                key={conversation.id}
                type="button"
                onClick={() => onSelect(conversation.id)}
                disabled={disabled}
                className={cn(
                  "truncate rounded-md px-3 py-2 text-left text-sm hover:bg-accent hover:text-accent-foreground",
                  selectedId === conversation.id && "bg-accent text-accent-foreground",
                )}
              >
                {conversation.title || "Untitled conversation"}
              </button>
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
