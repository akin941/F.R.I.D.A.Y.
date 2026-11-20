"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { ConversationHeader } from "@/components/chat/ConversationHeader";
import { ConversationList } from "@/components/chat/ConversationList";
import { MessageList } from "@/components/chat/MessageList";
import { Composer } from "@/components/chat/Composer";
import {
  dismissDraft,
  loadDismissedDrafts,
  taskDraftFromMessage,
  toolDraftFromMessage,
  type TaskDraft,
  type ToolRequestDraft,
} from "@/components/chat/drafts";
import { mapErrorType } from "@/components/chat/errors";
import { isTurnSettled, mergeTurn, type LiveTurn } from "@/components/chat/reconcile";
import { TaskDraftCard } from "@/components/chat/TaskDraftCard";
import { ToolRequestCard, ToolRequestNotice } from "@/components/chat/ToolRequestCard";
import type { ChatMessageView } from "@/components/chat/types";
import { localDateString } from "@/components/tasks/TodayView";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiFetch, apiPostJson, ApiError, errorMessage } from "@/lib/api-client";
import {
  fetchMessages,
  useConversations,
  useMessages,
  useUpdateConversation,
  type ConversationPatch,
} from "@/lib/query/chat";
import { queryKeys } from "@/lib/query/keys";
import { tr } from "@/lib/i18n/tr";
import { SSEStreamParser } from "@/lib/sse";
import type { ApiApprovalRequest, ApiConversation, ApiMessage, ApiTask } from "@/types/api";

/**
 * After a stream ends without its assistant row in the history (abort,
 * dropped connection, stream error), the history is refetched at these
 * delays: the server writes the `partial`/`failed` row only once it notices
 * the disconnect, which can lag the client's abort.
 */
export const RECOVERY_DELAYS_MS = [0, 1000, 3000];

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

interface LiveToolDraft {
  draft: ToolRequestDraft;
  created: ApiApprovalRequest | null;
}

export function ChatView() {
  const { activeWorkspace } = useWorkspace();
  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {tr.common.noWorkspace}
      </div>
    );
  }
  // Keyed by workspace: a switch unmounts the previous workspace's chat --
  // which aborts its stream -- and starts from a clean selection; the query
  // keys isolate the cached rows themselves.
  return <ChatWorkspace key={activeWorkspace.id} workspaceId={activeWorkspace.id} />;
}

function ChatWorkspace({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const conversations = useConversations(workspaceId);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const messages = useMessages(workspaceId, selectedId);
  const updateConversation = useUpdateConversation(workspaceId);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [composerValue, setComposerValue] = useState("");
  const [turn, setTurn] = useState<LiveTurn | null>(null);
  // Drafts that arrived over this tab's stream. A task draft shows at the
  // end until its persisted message is in the history (which then renders
  // it); a tool draft stays interactive for this session only.
  const [liveTaskDraft, setLiveTaskDraft] = useState<TaskDraft | null>(null);
  const [liveToolDrafts, setLiveToolDrafts] = useState<Record<string, LiveToolDraft>>({});
  const [confirmedDrafts, setConfirmedDrafts] = useState<Record<string, string>>({});
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(loadDismissedDrafts);
  const [bannerError, setBannerError] = useState<string | null>(null);
  const [disconnected, setDisconnected] = useState(false);
  const [sendUnavailable, setSendUnavailable] = useState(false);
  const [creatingChat, setCreatingChat] = useState(false);
  const [followSignal, setFollowSignal] = useState(0);

  const abortControllerRef = useRef<AbortController | null>(null);
  // Synchronous double-invocation guards: `isStreaming`/`creatingChat` state
  // updates are batched, so two rapid clicks in the same tick can both read
  // the pre-update value before a re-render lands. Refs close that window.
  const sendingRef = useRef(false);
  const creatingChatRef = useRef(false);
  // The live turn's id: a recovery loop for a superseded turn stops.
  const turnIdRef = useRef<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // Leaving the workspace (or the page) must not leave a stream running.
      abortControllerRef.current?.abort();
    };
  }, []);

  const isStreaming = turn?.phase === "streaming";
  const conversationList = conversations.data ?? [];
  const selectedConversation = conversationList.find((c) => c.id === selectedId) ?? null;
  const messagesUnavailable = messages.error instanceof ApiError && messages.error.status === 404;
  const unavailable = sendUnavailable || messagesUnavailable;

  function selectConversation(id: string | null) {
    setSelectedId(id);
    setTurn(null);
    turnIdRef.current = null;
    setLiveTaskDraft(null);
    setLiveToolDrafts({});
    setBannerError(null);
    setDisconnected(false);
    setSendUnavailable(false);
    setDrawerOpen(false);
  }

  function handleSelect(id: string) {
    if (isStreaming) return;
    selectConversation(id);
  }

  async function handleNewChat() {
    if (creatingChatRef.current || isStreaming) return;
    creatingChatRef.current = true;
    setCreatingChat(true);
    try {
      const conversation = await apiPostJson<ApiConversation>("/api/conversations", {
        workspace: workspaceId,
        title: "",
      });
      if (!mountedRef.current) return;
      queryClient.setQueryData<ApiConversation[]>(
        queryKeys.conversations(workspaceId),
        (prev = []) => [conversation, ...prev.filter((c) => c.id !== conversation.id)],
      );
      selectConversation(conversation.id);
    } catch {
      if (mountedRef.current) setBannerError(tr.chat.newChatFailed);
    } finally {
      creatingChatRef.current = false;
      if (mountedRef.current) setCreatingChat(false);
    }
  }

  /** Refetch until the turn's rows are in the history (or give up). */
  async function recover(
    conversationId: string,
    turnId: string,
    ref: Pick<LiveTurn, "baselineIds" | "doneMessageId">,
  ) {
    const key = queryKeys.messages(workspaceId, conversationId);
    for (const delay of RECOVERY_DELAYS_MS) {
      if (delay > 0) await sleep(delay);
      if (!mountedRef.current || turnIdRef.current !== turnId) return;
      try {
        await queryClient.fetchQuery({
          queryKey: key,
          queryFn: () => fetchMessages(conversationId),
          staleTime: 0,
        });
      } catch {
        // Keep the local bubbles; the next attempt (or a manual refresh)
        // may succeed.
      }
      const server = queryClient.getQueryData<ApiMessage[]>(key) ?? [];
      if (isTurnSettled(server, ref)) return;
    }
  }

  async function handleSend(retryContent?: string) {
    const content = retryContent ?? composerValue;
    const history = messages.data;
    if (sendingRef.current || isStreaming || !selectedId || !history || !content.trim()) {
      return;
    }
    sendingRef.current = true;
    const conversationId = selectedId;
    const turnId = `local-${Date.now()}`;
    const baselineIds = new Set(history.map((m) => m.id));

    if (retryContent === undefined) setComposerValue("");
    setBannerError(null);
    setDisconnected(false);
    // A new turn supersedes the previous turn's live drafts; persisted
    // drafts keep rendering from the history.
    setLiveTaskDraft(null);
    turnIdRef.current = turnId;
    setTurn({
      id: turnId,
      conversationId,
      baselineIds,
      content,
      assistantText: "",
      phase: "streaming",
      doneMessageId: null,
    });
    setFollowSignal((n) => n + 1);

    const controller = new AbortController();
    abortControllerRef.current = controller;
    let outcome: "done" | "error" | "aborted" | "disconnected" | "rejected" = "disconnected";
    let doneMessageId: string | null = null;
    const updateTurn = (update: (t: LiveTurn) => LiveTurn) =>
      setTurn((prev) => (prev && prev.id === turnId ? update(prev) : prev));

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
        outcome = "rejected";
        if (retryContent === undefined) setComposerValue(content);
        if (response.status === 404) {
          setSendUnavailable(true);
        } else if (response.status === 429) {
          setBannerError(tr.chat.throttled(response.headers.get("Retry-After")));
        } else if (response.status === 400) {
          const body = await response.json().catch(() => ({}));
          setBannerError(errorMessage(new ApiError(response.status, body), tr.chat.sendFailed));
        } else {
          setBannerError(tr.errors.generic);
        }
        return;
      }
      if (!response.body) return;

      const reader = response.body.getReader();
      const parser = new SSEStreamParser();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const event of parser.push(value)) {
          if (event.type === "chunk") {
            updateTurn((t) => ({ ...t, assistantText: t.assistantText + event.text }));
          } else if (event.type === "done") {
            outcome = "done";
            doneMessageId = event.message_id;
            // A personal read turned the conversation private + personal
            // server-side (one-way): refresh its flags for the header.
            if (event.connector_reads?.some((read) => read.data_class === "personal")) {
              void queryClient.invalidateQueries({
                queryKey: queryKeys.conversations(workspaceId),
                exact: true,
              });
            }
          } else if (event.type === "error") {
            outcome = "error";
            setBannerError(mapErrorType(event.error_type));
          } else if (event.type === "draft" && event.kind === "task") {
            // Non-terminal -- the stream keeps going until `done` (MVP 6 §5).
            setLiveTaskDraft({
              sourceMessageId: event.source_message_id,
              title: event.title,
              dueDate: event.due_date,
            });
          } else if (event.type === "draft" && event.kind === "tool") {
            const draft: ToolRequestDraft = {
              sourceMessageId: event.source_message_id,
              toolName: event.tool_name,
              payload: event.payload,
            };
            setLiveToolDrafts((prev) => ({
              ...prev,
              [draft.sourceMessageId]: { draft, created: null },
            }));
          }
        }
      }
    } catch (err) {
      outcome = err instanceof DOMException && err.name === "AbortError" ? "aborted" : "disconnected";
    } finally {
      sendingRef.current = false;
      if (abortControllerRef.current === controller) abortControllerRef.current = null;
      if (outcome === "rejected") {
        turnIdRef.current = null;
        setTurn((prev) => (prev?.id === turnId ? null : prev));
      } else if (mountedRef.current) {
        updateTurn((t) => ({
          ...t,
          phase: outcome === "done" ? "done" : "interrupted",
          doneMessageId,
        }));
        if (outcome === "disconnected") setDisconnected(true);
        void recover(conversationId, turnId, { baselineIds, doneMessageId });
      }
    }
  }

  function handleStop() {
    abortControllerRef.current?.abort();
  }

  function refreshHistory() {
    setDisconnected(false);
    if (selectedId) {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.messages(workspaceId, selectedId),
        exact: true,
      });
    }
  }

  function handleDismiss(messageId: string) {
    setDismissed((prev) => dismissDraft(prev, messageId));
    setLiveToolDrafts((prev) => {
      const next = { ...prev };
      delete next[messageId];
      return next;
    });
    setLiveTaskDraft((prev) => (prev?.sourceMessageId === messageId ? null : prev));
  }

  function handleTaskConfirmed(messageId: string, task: ApiTask) {
    setConfirmedDrafts((prev) => ({ ...prev, [messageId]: task.id }));
    refreshHistory();
  }

  const updateConversationFor = (patch: ConversationPatch) =>
    selectedId
      ? updateConversation.mutateAsync({ id: selectedId, patch })
      : Promise.resolve(undefined);

  // --- rendering -----------------------------------------------------------

  const visible = selectedId && messages.data ? mergeTurn(messages.data, turn) : [];
  const persistedIds = new Set((messages.data ?? []).map((m) => m.id));

  function renderTaskDraft(draft: TaskDraft, confirmedTaskId: string | null) {
    return (
      <TaskDraftCard
        key={`task-${draft.sourceMessageId}`}
        draft={draft}
        workspaceId={workspaceId}
        confirmedTaskId={confirmedTaskId}
        onConfirmed={(task) => handleTaskConfirmed(draft.sourceMessageId, task)}
        onAlreadyConfirmed={refreshHistory}
        onDiscard={() => handleDismiss(draft.sourceMessageId)}
      />
    );
  }

  function renderToolDraft(draft: ToolRequestDraft) {
    const live = liveToolDrafts[draft.sourceMessageId];
    if (!live) {
      return (
        <ToolRequestNotice
          key={`tool-${draft.sourceMessageId}`}
          draft={draft}
          workspaceId={workspaceId}
        />
      );
    }
    return (
      <ToolRequestCard
        key={`tool-${draft.sourceMessageId}`}
        draft={live.draft}
        workspaceId={workspaceId}
        created={live.created}
        onCreated={(request) =>
          setLiveToolDrafts((prev) => ({
            ...prev,
            [draft.sourceMessageId]: { draft: live.draft, created: request },
          }))
        }
        onDiscard={() => handleDismiss(draft.sourceMessageId)}
      />
    );
  }

  // Drafts rebuilt from persisted user messages, placed after that turn's
  // reply (or after the message itself if no reply exists yet).
  const extrasAfter = new Map<string, React.ReactNode>();
  const extraKeys: string[] = [];
  visible.forEach((message, index) => {
    if (dismissed.has(message.id)) return;
    const taskDraft = taskDraftFromMessage(message);
    const toolDraft = taskDraft ? null : toolDraftFromMessage(message);
    if (!taskDraft && !toolDraft) return;
    const next = visible[index + 1];
    const anchor = next && next.role === "assistant" ? next.id : message.id;
    const confirmedTaskId = taskDraft
      ? (message.confirmedTask ?? confirmedDrafts[message.id] ?? null)
      : null;
    extrasAfter.set(
      anchor,
      taskDraft ? renderTaskDraft(taskDraft, confirmedTaskId) : renderToolDraft(toolDraft!),
    );
    extraKeys.push(`${message.id}:${confirmedTaskId ?? ""}`);
  });

  // Live drafts whose message is not in the history yet show at the end.
  const footer: React.ReactNode[] = [];
  if (
    liveTaskDraft &&
    !persistedIds.has(liveTaskDraft.sourceMessageId) &&
    !dismissed.has(liveTaskDraft.sourceMessageId)
  ) {
    footer.push(
      renderTaskDraft(liveTaskDraft, confirmedDrafts[liveTaskDraft.sourceMessageId] ?? null),
    );
    extraKeys.push(`live-task:${liveTaskDraft.sourceMessageId}`);
  }
  for (const [messageId, live] of Object.entries(liveToolDrafts)) {
    if (persistedIds.has(messageId) || dismissed.has(messageId)) continue;
    footer.push(renderToolDraft(live.draft));
    extraKeys.push(`live-tool:${messageId}`);
  }

  function retryFor(message: ChatMessageView) {
    if (isStreaming || message.role !== "assistant") return undefined;
    if (message.status !== "partial" && message.status !== "failed") return undefined;
    const index = visible.indexOf(message);
    if (index !== visible.length - 1) return undefined;
    const prompt = visible
      .slice(0, index)
      .reverse()
      .find((m) => m.role === "user");
    if (!prompt) return undefined;
    return () => void handleSend(prompt.content);
  }

  const list = (
    <ConversationList
      conversations={conversationList}
      selectedId={selectedId}
      onSelect={handleSelect}
      onNewChat={() => void handleNewChat()}
      loading={conversations.isPending}
      error={conversations.error}
      onRetry={() => void conversations.refetch()}
      disabled={isStreaming || creatingChat}
    />
  );

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <aside
        data-testid="conversation-column"
        className="hidden w-64 shrink-0 border-r border-border md:flex md:flex-col"
      >
        {list}
      </aside>
      {/* Below `md` the list lives in a drawer opened from the header. */}
      <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
        <SheetContent side="left" className="w-72 max-w-[85vw] gap-0 p-0 pt-10">
          <SheetTitle className="sr-only">{tr.chat.conversations}</SheetTitle>
          {list}
        </SheetContent>
      </Sheet>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div aria-live="polite" className="sr-only">
          {isStreaming ? tr.chat.assistantTyping : ""}
        </div>
        <ConversationHeader
          conversation={selectedConversation}
          onOpenList={() => setDrawerOpen(true)}
          onUpdate={updateConversationFor}
          actionsDisabled={isStreaming}
        />
        {selectedConversation?.personal_data ? (
          <p
            role="status"
            data-testid="personal-data-notice"
            className="mx-4 mt-3 rounded-md border border-warning/30 bg-warning-muted px-3 py-2 text-xs text-warning"
          >
            {tr.chat.personalDataNotice}
          </p>
        ) : null}
        {bannerError ? (
          <Alert variant="destructive" role="alert" className="mx-4 mt-3 w-auto">
            <AlertDescription>{bannerError}</AlertDescription>
          </Alert>
        ) : null}
        {disconnected ? (
          <Alert className="mx-4 mt-3 w-auto">
            <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
              <span>{tr.chat.disconnected}</span>
              <Button variant="outline" size="sm" onClick={refreshHistory}>
                {tr.chat.refreshHistory}
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        {unavailable ? (
          <Alert variant="destructive" role="alert" className="mx-4 mt-3 w-auto">
            <AlertDescription>{tr.chat.unavailable}</AlertDescription>
          </Alert>
        ) : null}

        {!selectedId ? (
          <div className="flex flex-1 items-center justify-center p-6 text-center text-sm text-muted-foreground">
            {tr.chat.pickConversation}
          </div>
        ) : unavailable ? (
          <div className="flex-1" />
        ) : messages.isPending ? (
          <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-6">
            <Skeleton className="h-10 w-2/3 self-end" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : messages.error && !messages.data ? (
          <div className="p-4">
            <ErrorState
              error={messages.error}
              title={tr.chat.loadMessagesFailed}
              onRetry={() => void messages.refetch()}
            />
          </div>
        ) : visible.length === 0 ? (
          <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
            {tr.chat.emptyConversation}
          </div>
        ) : (
          <MessageList
            key={selectedId}
            messages={visible}
            workspaceId={workspaceId}
            renderAfter={(message) => extrasAfter.get(message.id)}
            extrasKey={extraKeys.join("|")}
            footer={footer.length > 0 ? footer : null}
            followSignal={followSignal}
            retryFor={retryFor}
          />
        )}

        {selectedId && !unavailable ? (
          <Composer
            value={composerValue}
            onChange={setComposerValue}
            onSend={() => void handleSend()}
            onStop={handleStop}
            isStreaming={isStreaming}
            disabled={!messages.data}
          />
        ) : null}
      </div>
    </div>
  );
}
