"use client";

import { useEffect, useRef } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { ChatMessageView } from "@/components/chat/types";

export function MessageList({ messages }: { messages: ChatMessageView[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // jsdom (used in tests) does not implement scrollIntoView at all.
    if (typeof bottomRef.current?.scrollIntoView === "function") {
      bottomRef.current.scrollIntoView({ block: "end" });
    }
  }, [messages]);

  return (
    <ScrollArea className="min-h-0 flex-1 p-4">
      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        {messages.map((message) => (
          <div
            key={message.id}
            className={cn(
              "flex flex-col gap-1 rounded-lg px-4 py-2 text-sm whitespace-pre-wrap",
              message.role === "user"
                ? "self-end bg-primary text-primary-foreground"
                : "self-start bg-muted text-foreground",
            )}
          >
            {message.content}
            {message.status === "partial" || message.status === "failed" ? (
              <span className="text-xs opacity-70">
                {message.status === "failed" ? "Başarısız" : "Yarım yanıt"}
              </span>
            ) : null}
            {message.role === "assistant" && (message.memoriesUsed?.length ?? 0) > 0 ? (
              <span className="text-xs opacity-70">
                Hafıza bağlamı: {message.memoriesUsed?.length} not
              </span>
            ) : null}
            {message.role === "assistant" && (message.chunksUsed?.length ?? 0) > 0 ? (
              <span className="text-xs opacity-70">
                Bilgi bağlamı: {message.chunksUsed?.length} parça
              </span>
            ) : null}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  );
}
