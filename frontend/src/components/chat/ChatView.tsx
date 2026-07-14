"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConversationList } from "@/components/chat/ConversationList";
import { MessageList } from "@/components/chat/MessageList";
import { Composer } from "@/components/chat/Composer";
import { mapErrorType } from "@/components/chat/errors";
import type { ChatMessageView } from "@/components/chat/types";
import { TaskDraftCard, type TaskDraft } from "@/components/chat/TaskDraftCard";
import { ToolRequestCard, type ToolRequestDraft } from "@/components/chat/ToolRequestCard";
import { localDateString } from "@/components/tasks/TodayView";
import { apiFetch, apiGetJson, apiPostJson, ApiError } from "@/lib/api-client";
import { SSEStreamParser } from "@/lib/sse";
import { useWorkspace } from "@/contexts/workspace-context";
import type { ApiConversation, ApiMessage } from "@/types/api";

function toView(message: ApiMessage): ChatMessageView {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    status: message.status,
    memoriesUsed: message.memories_used,
    chunksUsed: message.chunks_used,
  };
}

export function ChatView() {
  const { activeWorkspace, user } = useWorkspace();

  const [conversations, setConversations] = useState<ApiConversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(false);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);

  const [messages, setMessages] = useState<ChatMessageView[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [conversationUnavailable, setConversationUnavailable] = useState(false);

  const [composerValue, setComposerValue] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [optimisticUser, setOptimisticUser] = useState<ChatMessageView | null>(null);
  const [streamingAssistant, setStreamingAssistant] = useState<ChatMessageView | null>(null);
  const [pendingDraft, setPendingDraft] = useState<TaskDraft | null>(null);
  const [pendingToolRequest, setPendingToolRequest] = useState<ToolRequestDraft | null>(null);
  const [bannerError, setBannerError] = useState<string | null>(null);
  const [disconnected, setDisconnected] = useState(false);
  const [creatingChat, setCreatingChat] = useState(false);

  const abortControllerRef = useRef<AbortController | null>(null);
  // Synchronous double-invocation guards: `isStreaming`/`creatingChat` state
  // updates are batched, so two rapid clicks in the same tick can both read
  // the pre-update value before a re-render lands. Refs close that window.
  const sendingRef = useRef(false);
  const creatingChatRef = useRef(false);
  // Track the workspace/conversation a given async load was issued for, so a
  // response that resolves after the user has already switched away can
  // detect it's stale and bail out instead of overwriting newer state.
  const activeWorkspaceIdRef = useRef<string | null>(null);
  const selectedConversationIdRef = useRef<string | null>(null);

  // Updates the ref synchronously at the exact moment the selection changes,
  // not via a useEffect: an effect only runs after the render commits, which
  // leaves a window where an in-flight loadMessages for the *previous*
  // conversation could resolve, read the still-stale ref, and apply itself.
  function selectConversation(id: string | null) {
    selectedConversationIdRef.current = id;
    setSelectedConversationId(id);
  }

  const loadConversations = useCallback(async (workspaceId: string) => {
    setConversationsLoading(true);
    try {
      const list = await apiGetJson<ApiConversation[]>(
        `/api/conversations?workspace=${encodeURIComponent(workspaceId)}`,
      );
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setConversations(list);
    } catch {
      if (activeWorkspaceIdRef.current === workspaceId) setConversations([]);
    } finally {
      if (activeWorkspaceIdRef.current === workspaceId) setConversationsLoading(false);
    }
  }, []);

  const loadMessages = useCallback(async (conversationId: string) => {
    setMessagesLoading(true);
    try {
      const list = await apiGetJson<ApiMessage[]>(`/api/conversations/${conversationId}/messages`);
      if (selectedConversationIdRef.current !== conversationId) return;
      setMessages(list.map(toView));
      setConversationUnavailable(false);
    } catch (err) {
      if (selectedConversationIdRef.current !== conversationId) return;
      if (err instanceof ApiError && err.status === 404) {
        setConversationUnavailable(true);
        setMessages([]);
      }
    } finally {
      if (selectedConversationIdRef.current === conversationId) setMessagesLoading(false);
    }
  }, []);

  // Workspace switches must not leak the previous workspace's conversations,
  // and must not leave a stream running against the workspace being left.
  /* eslint-disable react-hooks/set-state-in-effect -- synchronizing local state with the active workspace prop, not a subscription */
  useEffect(() => {
    const workspaceId = activeWorkspace?.id ?? null;
    activeWorkspaceIdRef.current = workspaceId;
    abortControllerRef.current?.abort();
    selectConversation(null);
    setMessages([]);
    setConversationUnavailable(false);
    setPendingDraft(null);
    setPendingToolRequest(null);
    if (workspaceId) {
      void loadConversations(workspaceId);
    } else {
      setConversations([]);
    }
  }, [activeWorkspace, loadConversations]);
  /* eslint-enable react-hooks/set-state-in-effect */

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount/selection-change, the standard data-fetching effect pattern */
  useEffect(() => {
    if (selectedConversationId) {
      void loadMessages(selectedConversationId);
    }
  }, [selectedConversationId, loadMessages]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function handleNewChat() {
    if (creatingChatRef.current || !activeWorkspace || isStreaming) return;
    creatingChatRef.current = true;
    setCreatingChat(true);
    try {
      const conversation = await apiPostJson<ApiConversation>("/api/conversations", {
        workspace: activeWorkspace.id,
        title: "",
      });
      if (activeWorkspaceIdRef.current !== activeWorkspace.id) return;
      setConversations((prev) => [conversation, ...prev]);
      selectConversation(conversation.id);
    } finally {
      creatingChatRef.current = false;
      setCreatingChat(false);
    }
  }

  function handleSelect(id: string) {
    if (isStreaming) return;
    setPendingDraft(null);
    setPendingToolRequest(null);
    selectConversation(id);
  }

  async function handleSend() {
    if (
      sendingRef.current ||
      isStreaming ||
      !selectedConversationId ||
      !composerValue.trim()
    ) {
      return;
    }
    sendingRef.current = true;
    const content = composerValue;
    const conversationId = selectedConversationId;

    setComposerValue("");
    setBannerError(null);
    setDisconnected(false);
    // A new turn supersedes any un-actioned draft from an earlier turn --
    // deduplication of an already-confirmed draft is explicitly out of
    // scope (MVP 6 §1's accepted duplicate-on-reload note), but a stale
    // card just sitting under a newer reply would be confusing regardless.
    setPendingDraft(null);
    setPendingToolRequest(null);
    setOptimisticUser({ id: `local-user-${Date.now()}`, role: "user", content, status: "complete" });
    setStreamingAssistant({ id: `local-assistant-${Date.now()}`, role: "assistant", content: "", status: "partial" });
    setIsStreaming(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const response = await apiFetch(`/api/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // client_local_date mirrors /today's own localDateString() convention
        // (MVP 6 §3) -- the reference date a task_draft extraction resolves
        // relative dates against; the server never computes "today" itself.
        body: JSON.stringify({ content, client_local_date: localDateString(new Date()) }),
        signal: controller.signal,
      });

      if (!response.ok) {
        setComposerValue(content);
        if (response.status === 404) {
          setConversationUnavailable(true);
          selectConversation(null);
        } else if (response.status === 429) {
          const retryAfter = response.headers.get("Retry-After");
          setBannerError(
            retryAfter
              ? `Çok hızlı mesaj gönderiyorsun. ${retryAfter}s sonra tekrar dene.`
              : "Çok hızlı mesaj gönderiyorsun. Lütfen biraz bekle.",
          );
        } else if (response.status === 400) {
          const body = await response.json().catch(() => ({}));
          setBannerError(body.detail ?? "Mesaj gönderilemedi.");
        } else {
          setBannerError("Bir şeyler ters gitti. Lütfen tekrar deneyin.");
        }
        return;
      }

      if (!response.body) {
        setDisconnected(true);
        return;
      }

      const reader = response.body.getReader();
      const parser = new SSEStreamParser();
      let sawTerminal = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const event of parser.push(value)) {
          if (event.type === "chunk") {
            setStreamingAssistant((prev) =>
              prev ? { ...prev, content: prev.content + event.text } : prev,
            );
          } else if (event.type === "done") {
            sawTerminal = true;
          } else if (event.type === "error") {
            sawTerminal = true;
            setBannerError(mapErrorType(event.error_type));
          } else if (event.type === "draft" && event.kind === "task") {
            // Non-terminal -- must NOT set sawTerminal; the stream keeps
            // going until `done` (MVP 6 §5).
            setPendingDraft({
              sourceMessageId: event.source_message_id,
              title: event.title,
              dueDate: event.due_date,
            });
          } else if (event.type === "draft" && event.kind === "tool") {
            setPendingToolRequest({
              sourceMessageId: event.source_message_id,
              toolName: event.tool_name,
              payload: event.payload,
            });
          }
        }
      }
      if (!sawTerminal) {
        setDisconnected(true);
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        setDisconnected(true);
      }
    } finally {
      sendingRef.current = false;
      setIsStreaming(false);
      setOptimisticUser(null);
      setStreamingAssistant(null);
      abortControllerRef.current = null;
      if (conversationId) {
        void loadMessages(conversationId);
      }
    }
  }

  function handleStop() {
    abortControllerRef.current?.abort();
  }

  const visibleMessages: ChatMessageView[] = [
    ...messages,
    ...(optimisticUser ? [optimisticUser] : []),
    ...(streamingAssistant ? [streamingAssistant] : []),
  ];

  return (
    <div className="flex flex-1 overflow-hidden">
      <ConversationList
        conversations={conversations}
        selectedId={selectedConversationId}
        onSelect={handleSelect}
        onNewChat={() => void handleNewChat()}
        loading={conversationsLoading}
        disabled={isStreaming || creatingChat || !activeWorkspace}
      />
      <div className="flex flex-1 flex-col">
        <div aria-live="polite" className="sr-only">
          {isStreaming ? "Asistan yanıtlıyor" : ""}
        </div>
        {bannerError ? (
          <Alert variant="destructive" role="alert" className="m-4">
            <AlertDescription>{bannerError}</AlertDescription>
          </Alert>
        ) : null}
        {disconnected ? (
          <Alert className="m-4">
            <AlertDescription className="flex items-center justify-between gap-4">
              <span>Yanıt tamamlanmadan bağlantı koptu.</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setDisconnected(false);
                  if (selectedConversationId) void loadMessages(selectedConversationId);
                }}
              >
                Geçmişi yenile
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        {conversationUnavailable ? (
          <Alert variant="destructive" role="alert" className="m-4">
            <AlertDescription>Bu sohbete artık erişilemiyor.</AlertDescription>
          </Alert>
        ) : null}
        {!activeWorkspace ? (
          <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
            Kullanılabilir çalışma alanı yok.
          </div>
        ) : !selectedConversationId ? (
          <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
            Bir sohbet seç ya da yeni bir sohbet başlat.
          </div>
        ) : (
          <>
            {messagesLoading ? null : <MessageList messages={visibleMessages} />}
            {pendingDraft && activeWorkspace ? (
              <TaskDraftCard
                draft={pendingDraft}
                workspaceId={activeWorkspace.id}
                onConfirmed={() => setPendingDraft(null)}
                onDiscard={() => setPendingDraft(null)}
              />
            ) : null}
            {pendingToolRequest && activeWorkspace ? (
              <ToolRequestCard
                draft={pendingToolRequest}
                workspaceId={activeWorkspace.id}
                currentUserId={user?.id ?? null}
                onDiscard={() => setPendingToolRequest(null)}
              />
            ) : null}
            <Composer
              value={composerValue}
              onChange={setComposerValue}
              onSend={() => void handleSend()}
              onStop={handleStop}
              isStreaming={isStreaming}
              disabled={!selectedConversationId}
            />
          </>
        )}
      </div>
    </div>
  );
}
