"use client";

import { useEffect, useRef, useState } from "react";
import { Mic } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { JarvisOrb, type JarvisOrbState } from "@/components/voice/JarvisOrb";
import { mapErrorType } from "@/components/chat/errors";
import { apiFetch, apiPostJson } from "@/lib/api-client";
import { SSEStreamParser } from "@/lib/sse";
import { useWorkspace } from "@/contexts/workspace-context";
import type { ApiConversation } from "@/types/api";

/**
 * MVP 12 §1 (ADR-007): a real integration, not a mockup — sends through the
 * exact same `/api/conversations/{id}/messages` SSE endpoint ChatView.tsx
 * uses, and drives the orb's state off the exact same event types
 * (ChatView.tsx's chunk/done/error handling). Mic capture and real audio
 * playback are MVP 12 §2-§3 (docs/prompts/mvp-12-voice/brief.md) — not
 * built yet; the mic button here is a visible placeholder.
 */
export function VoiceView() {
  const { activeWorkspace } = useWorkspace();

  const [creatingConversation, setCreatingConversation] = useState(false);
  const [value, setValue] = useState("");
  const [transcript, setTranscript] = useState("");
  const [orbState, setOrbState] = useState<JarvisOrbState>("idle");
  const [isSending, setIsSending] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const conversationIdRef = useRef<string | null>(null);
  const sendingRef = useRef(false);

  useEffect(() => {
    conversationIdRef.current = null;
  }, [activeWorkspace?.id]);

  async function ensureConversation(): Promise<string | null> {
    if (conversationIdRef.current) return conversationIdRef.current;
    if (!activeWorkspace) return null;
    setCreatingConversation(true);
    try {
      const conversation = await apiPostJson<ApiConversation>("/api/conversations", {
        workspace: activeWorkspace.id,
        title: "Sesli",
      });
      conversationIdRef.current = conversation.id;
      return conversation.id;
    } catch {
      setErrorMessage("Konuşma başlatılamadı. Lütfen tekrar deneyin.");
      return null;
    } finally {
      setCreatingConversation(false);
    }
  }

  async function handleSend() {
    if (sendingRef.current || isSending || !value.trim()) return;
    const content = value;
    setValue("");
    setErrorMessage(null);
    setTranscript("");

    const id = await ensureConversation();
    if (!id) return;

    sendingRef.current = true;
    setIsSending(true);
    setOrbState("thinking");

    try {
      const response = await apiFetch(`/api/conversations/${id}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });

      if (!response.ok || !response.body) {
        setErrorMessage("Bir şeyler ters gitti. Lütfen tekrar deneyin.");
        return;
      }

      const reader = response.body.getReader();
      const parser = new SSEStreamParser();
      let sawTerminal = false;

      while (true) {
        const { done, value: chunkValue } = await reader.read();
        if (done) break;
        for (const event of parser.push(chunkValue)) {
          if (event.type === "chunk") {
            setOrbState("speaking");
            setTranscript((prev) => prev + event.text);
          } else if (event.type === "done") {
            sawTerminal = true;
          } else if (event.type === "error") {
            sawTerminal = true;
            setErrorMessage(mapErrorType(event.error_type));
          }
        }
      }
      if (!sawTerminal) {
        setErrorMessage("Bağlantı kesildi.");
      }
    } catch {
      setErrorMessage("Bir şeyler ters gitti. Lütfen tekrar deneyin.");
    } finally {
      sendingRef.current = false;
      setIsSending(false);
      setOrbState("idle");
    }
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!isSending && value.trim()) void handleSend();
    }
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-8 p-8">
      <JarvisOrb state={orbState} size={220} />

      {transcript ? (
        <p className="max-w-xl text-center text-sm text-muted-foreground">{transcript}</p>
      ) : null}

      {errorMessage ? (
        <Alert variant="destructive" className="max-w-xl">
          <AlertDescription>{errorMessage}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex w-full max-w-xl items-end gap-2">
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Jarvis'e yaz…"
          disabled={isSending || creatingConversation}
          rows={2}
          className="flex-1 resize-none"
        />
        <Button
          type="button"
          variant="outline"
          size="icon"
          disabled
          title="Sesli giriş yakında (MVP 12 §2)"
          aria-label="Sesli giriş yakında"
        >
          <Mic className="size-4" />
        </Button>
        <Button
          type="button"
          onClick={() => void handleSend()}
          disabled={isSending || creatingConversation || !value.trim()}
        >
          Gönder
        </Button>
      </div>
    </div>
  );
}
