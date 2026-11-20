"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Mic, MicOff, Square } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { FridayOrb, type FridayOrbState } from "@/components/voice/FridayOrb";
import { useVoiceWake } from "@/components/voice/VoiceProvider";
import { WakeSettingsPanel } from "@/components/voice/WakeSettingsPanel";
import { TaskDraftCard } from "@/components/chat/TaskDraftCard";
import { ToolRequestCard } from "@/components/chat/ToolRequestCard";
import type { TaskDraft, ToolRequestDraft } from "@/components/chat/drafts";
import { mapErrorType } from "@/components/chat/errors";
import { localDateString } from "@/components/tasks/TodayView";
import { apiPostJson } from "@/lib/api-client";
import { isKnownErrorCode, messageForError } from "@/lib/i18n/error-codes";
import { tr } from "@/lib/i18n/tr";
import { useWorkspace } from "@/contexts/workspace-context";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";
import * as voiceApi from "@/lib/voice/api";
import { startCapture } from "@/lib/voice/capture";
import { VOICE_FINISH_MS, VOICE_HOLD_THRESHOLD_MS } from "@/lib/voice/config";
import { VoiceController, type VoiceControllerDeps, type VoiceSnapshot } from "@/lib/voice/controller";
import { readMuteState, setVoiceMuted, useVoiceMute } from "@/lib/voice/mute";
import { clearVoicePresence, publishVoicePresence } from "@/lib/voice/presence";
import { holdListeningTitle } from "@/lib/voice/title-indicator";
import type { VoicePhase } from "@/lib/voice/voice-machine";
import type { ApiApprovalRequest, ApiConversation, ApiMemory } from "@/types/api";

/**
 * The push-to-talk voice page (Phase 6 branch 3). Voice is a transport onto
 * the unchanged chat turn (D1): the transcript goes through the same
 * messages endpoint with `input_mode="voice"`, and the reply is spoken
 * sentence by sentence while it streams.
 *
 * D8 / ADR-015: drafts from a voice turn render as the same cards chat
 * uses, confirmed only by a click on the card; Friday only *says* a
 * confirmation once the card hands back a receipt id. Nothing on this page
 * turns a transcript into a click.
 */

const PREVIEW_KEY = "friday:voice:preview";

function loadPreview(): boolean {
  try {
    return window.localStorage.getItem(PREVIEW_KEY) === "1";
  } catch {
    return false;
  }
}

function savePreview(value: boolean) {
  try {
    if (value) window.localStorage.setItem(PREVIEW_KEY, "1");
    else window.localStorage.removeItem(PREVIEW_KEY);
  } catch {
    // Per-device convenience only.
  }
}

function orbStateFor(phase: VoicePhase, wakeArmed: boolean): FridayOrbState {
  if (phase === "idle" && wakeArmed) return "armed";
  return phase === "finishing" ? "listening" : phase;
}

/** Turkish text for a voice error code (capture, catalog or stream). */
export function voiceErrorMessage(code: string): string {
  if (tr.voice.errors[code]) return tr.voice.errors[code];
  if (isKnownErrorCode(code)) return messageForError(code, 0);
  return mapErrorType(code);
}

interface LiveTool {
  draft: ToolRequestDraft;
  created: ApiApprovalRequest | null;
}

const EMPTY_SNAPSHOT: VoiceSnapshot = {
  phase: "idle",
  buffer: "",
  error: null,
  heard: "",
  reply: "",
  review: null,
  notice: null,
};

export interface VoiceViewProps {
  /** Test seam: replace the controller's media/network edges. */
  depsOverride?: Partial<VoiceControllerDeps>;
  /**
   * `page` (/voice, default) or `hud`: embedded in /hud (Phase 6 A8), the
   * same loop renders only its controls -- mic, stop, review, drafts and
   * notices -- while the HUD draws the orb, the state text and the captions
   * from the published voice presence.
   */
  variant?: "page" | "hud";
}

export function VoiceView({ depsOverride, variant = "page" }: VoiceViewProps = {}) {
  const full = variant === "page";
  const { activeWorkspace } = useWorkspace();
  const workspaceId = activeWorkspace?.id ?? null;
  const mute = useVoiceMute();
  const { muted, readable } = mute;
  const wake = useVoiceWake();
  const { registerTarget } = wake;
  const wakeArmed = wake.snapshot.state === "armed" || wake.snapshot.state === "paused";

  const [snapshot, setSnapshot] = useState<VoiceSnapshot>(EMPTY_SNAPSHOT);
  const [value, setValue] = useState("");
  const [reviewText, setReviewText] = useState("");
  const [preview, setPreview] = useState(false);
  const [taskDrafts, setTaskDrafts] = useState<TaskDraft[]>([]);
  const [toolDrafts, setToolDrafts] = useState<Record<string, LiveTool>>({});
  const [confirmed, setConfirmed] = useState<Record<string, string>>({});
  const [proposals, setProposals] = useState<ApiMemory[]>([]);

  const conversationIdRef = useRef<string | null>(null);
  const workspaceRef = useRef<string | null>(workspaceId);
  const previewRef = useRef(preview);
  const pressStartedRef = useRef(0);
  const toggledRef = useRef(false);
  const spaceDownRef = useRef(false);

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect -- one-time read of a per-device setting after mount (not available during prerender) */
    setPreview(loadPreview());
    /* eslint-enable react-hooks/set-state-in-effect */
  }, []);
  useEffect(() => {
    previewRef.current = preview;
  }, [preview]);

  const ensureConversation = useCallback(async (): Promise<string | null> => {
    if (conversationIdRef.current) return conversationIdRef.current;
    const workspace = workspaceRef.current;
    if (!workspace) return null;
    try {
      const conversation = await apiPostJson<ApiConversation>("/api/conversations", {
        workspace,
        title: "Sesli",
      });
      conversationIdRef.current = conversation.id;
      return conversation.id;
    } catch {
      return null;
    }
  }, []);

  // One controller per mounted page; its edges are the real browser APIs
  // and BFF routes unless a test overrides them. Constructing it touches no
  // browser API (the mic and AudioContext open on first use). The closures
  // below read refs only when the controller calls them (events, effects),
  // never during render.
  /* eslint-disable react-hooks/refs -- refs are read at call time, not during render */
  const [controller] = useState(() => {
    let lastReview: string | null = null;
    const deps: VoiceControllerDeps = {
      ensureConversation,
      workspaceId: () => workspaceRef.current,
      startCapture: (callbacks) => startCapture(callbacks),
      transcribe: voiceApi.transcribe,
      sendTurn: voiceApi.sendTurn,
      synthesize: voiceApi.synthesize,
      fetchReceipt: voiceApi.fetchReceipt,
      proposeMemory: voiceApi.proposeMemory,
      setMuted: setVoiceMuted,
      readMute: readMuteState,
      clientLocalDate: () => localDateString(new Date()),
      previewBeforeSend: () => previewRef.current,
      finishMs: VOICE_FINISH_MS,
      onSnapshot: (next) => {
        setSnapshot(next);
        // A new transcript held for review seeds the editor once.
        if (next.review !== lastReview) {
          lastReview = next.review;
          if (next.review !== null) setReviewText(next.review);
        }
      },
      onDraft: (event) => {
        if (event.kind === "task") {
          setTaskDrafts((prev) => [
            ...prev.filter((d) => d.sourceMessageId !== event.source_message_id),
            { sourceMessageId: event.source_message_id, title: event.title, dueDate: event.due_date },
          ]);
        } else {
          setToolDrafts((prev) => ({
            ...prev,
            [event.source_message_id]: {
              draft: {
                sourceMessageId: event.source_message_id,
                toolName: event.tool_name,
                payload: event.payload,
              },
              created: null,
            },
          }));
        }
      },
      onMemoryProposal: (memory) => setProposals((prev) => [memory, ...prev].slice(0, 5)),
      ...depsOverride,
    };
    return new VoiceController(deps);
  });
  /* eslint-enable react-hooks/refs */

  // Leaving the page stops the mic, the reply stream and playback.
  useEffect(() => {
    return () => controller.stop();
  }, [controller]);

  // The tab-wide voice presence (A8): the shell mini-orb and the HUD
  // mirror this loop; cleared when the loop unmounts.
  useEffect(() => {
    publishVoicePresence(
      {
        phase: snapshot.phase,
        heard: snapshot.buffer || snapshot.heard,
        reply: snapshot.reply,
        error: snapshot.error,
      },
      () => controller.level() ?? 0,
    );
  }, [snapshot, controller]);
  useEffect(() => () => clearVoicePresence(), []);

  // Wake mode (shell, branch 4) hands its detections to this page's loop.
  useEffect(() => {
    registerTarget(controller);
    return () => registerTarget(null);
  }, [controller, registerTarget]);

  // A workspace switch starts a fresh voice conversation.
  useEffect(() => {
    if (workspaceRef.current !== workspaceId) {
      workspaceRef.current = workspaceId;
      conversationIdRef.current = null;
      controller.resetConversation();
      setTaskDrafts([]);
      setToolDrafts({});
    }
  }, [workspaceId, controller]);

  // The shell mute toggle / another tab / an unreadable state (A6).
  useEffect(() => {
    if (muted) controller.muteChanged({ muted, readable });
  }, [muted, readable, controller]);

  const phase = snapshot.phase;
  const micOpen = phase === "listening";
  const busy = phase === "thinking" || phase === "speaking" || phase === "transcribing";

  // The mic indicator in the tab title while the mic is open (D6).
  useEffect(() => {
    if (!micOpen) return;
    return holdListeningTitle();
  }, [micOpen]);

  // Space held = push-to-talk; Escape = Durdur (D7).
  useEffect(() => {
    function isTyping(target: EventTarget | null) {
      const el = target as HTMLElement | null;
      return !!el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT" || el.isContentEditable);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        controller.stop();
        return;
      }
      if (event.code !== "Space" || event.repeat || isTyping(event.target)) return;
      if ((event.target as HTMLElement | null)?.tagName === "BUTTON") return;
      event.preventDefault();
      spaceDownRef.current = true;
      void controller.press();
    }
    function onKeyUp(event: KeyboardEvent) {
      if (event.code !== "Space" || !spaceDownRef.current) return;
      event.preventDefault();
      spaceDownRef.current = false;
      void controller.release();
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [controller]);

  // The mic button: hold to talk (pointer down -> up after the threshold),
  // or a short click to toggle (the next press ends the utterance).
  function handlePointerDown(event: React.PointerEvent) {
    if (event.button !== 0) return;
    if (toggledRef.current) {
      toggledRef.current = false;
      void controller.release();
      return;
    }
    pressStartedRef.current = performance.now();
    void controller.press();
  }

  function handlePointerUp() {
    if (pressStartedRef.current === 0) return;
    const held = performance.now() - pressStartedRef.current;
    pressStartedRef.current = 0;
    if (held >= VOICE_HOLD_THRESHOLD_MS) void controller.release();
    else toggledRef.current = true;
  }

  function handleClick(event: React.MouseEvent) {
    // Keyboard activation (Enter/Space on the focused button) is a toggle;
    // pointer clicks were handled by down/up above.
    if (event.detail !== 0) return;
    if (micOpen) void controller.release();
    else void controller.press();
  }

  // A toggle left open is closed by any phase change away from listening.
  useEffect(() => {
    if (!micOpen) toggledRef.current = false;
  }, [micOpen]);

  async function handleTypedSend() {
    if (!value.trim() || busy) return;
    const content = value;
    setValue("");
    await controller.sendText(content, "text");
  }

  async function handleReviewSend() {
    if (!reviewText.trim()) return;
    const content = reviewText;
    setReviewText("");
    await controller.sendText(content, "voice");
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleTypedSend();
    }
  }

  function dismissTask(id: string) {
    setTaskDrafts((prev) => prev.filter((d) => d.sourceMessageId !== id));
  }

  function dismissTool(id: string) {
    setToolDrafts((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  const errorText = phase === "error" && snapshot.error ? voiceErrorMessage(snapshot.error) : null;
  const noticeText = snapshot.notice ? (tr.voice.notices[snapshot.notice] ?? null) : null;
  const statusText = tr.voice.phase[phase];

  return (
    <div
      className={cn(
        full ? VIEW_SCROLL_ROOT : "flex w-full flex-col",
        "items-center justify-center-safe",
        full ? "gap-6 p-6 sm:p-8" : "gap-3",
      )}
      data-variant={variant}
    >
      {full ? (
        <>
          <FridayOrb state={orbStateFor(phase, wakeArmed)} size={220} level={() => controller.level() ?? 0} />
          <p aria-live="polite" role="status" data-testid="voice-status" className="text-center text-sm text-muted-foreground">
            {statusText}
          </p>
        </>
      ) : (
        // The HUD announces its own state (aria-live); this is the hint.
        <p data-testid="voice-status" className="text-center text-xs text-hud-caption-muted">
          {statusText}
        </p>
      )}

      {mute.muted ? (
        <Alert className="max-w-xl" data-testid="voice-muted">
          <AlertDescription className="flex flex-wrap items-center gap-2">
            <MicOff aria-hidden className="size-4" />
            <span>{mute.readable ? tr.voice.mutedNotice : tr.voice.errors.mute_unreadable}</span>
          </AlertDescription>
        </Alert>
      ) : null}

      {/* Captions are always on: what Friday heard and what it answers
          (on the HUD, the HUD's own captions panel draws them). */}
      {full && (snapshot.heard || snapshot.buffer || snapshot.reply) ? (
        <div className="flex w-full max-w-xl flex-col gap-2 text-sm" data-testid="voice-captions">
          {snapshot.heard || snapshot.buffer ? (
            <p className="text-right">
              <span className="sr-only">{tr.voice.heard}: </span>
              <span className="rounded-2xl bg-primary px-3 py-1.5 text-primary-foreground">
                {snapshot.buffer || snapshot.heard}
              </span>
            </p>
          ) : null}
          {snapshot.reply ? (
            <p className="text-muted-foreground" data-testid="voice-reply">
              <span className="sr-only">{tr.voice.reply}: </span>
              {snapshot.reply}
            </p>
          ) : null}
        </div>
      ) : null}

      {errorText ? (
        <Alert variant="destructive" role="alert" className="max-w-xl">
          <AlertDescription>{errorText}</AlertDescription>
        </Alert>
      ) : null}
      {noticeText && !errorText ? (
        <Alert className="max-w-xl" data-testid="voice-notice">
          <AlertDescription>{noticeText}</AlertDescription>
        </Alert>
      ) : null}

      {snapshot.review !== null ? (
        <div className="flex w-full max-w-xl flex-col gap-2" data-testid="voice-review">
          <label htmlFor="voice-review" className="text-xs text-muted-foreground">
            {tr.voice.reviewLabel}
          </label>
          <Textarea
            id="voice-review"
            value={reviewText}
            onChange={(e) => setReviewText(e.target.value)}
            rows={2}
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => controller.stop()}>
              {tr.voice.reviewDiscard}
            </Button>
            <Button size="sm" onClick={() => void handleReviewSend()} disabled={!reviewText.trim()}>
              {tr.voice.reviewSend}
            </Button>
          </div>
        </div>
      ) : null}

      {taskDrafts.length > 0 || Object.keys(toolDrafts).length > 0 ? (
        <section className="flex w-full max-w-xl flex-col gap-3" aria-label={tr.voice.draftsHeading}>
          <h2 className="text-xs font-medium text-muted-foreground">{tr.voice.draftsHeading}</h2>
          {workspaceId
            ? taskDrafts.map((draft) => (
                <TaskDraftCard
                  key={draft.sourceMessageId}
                  draft={draft}
                  workspaceId={workspaceId}
                  confirmedTaskId={confirmed[draft.sourceMessageId] ?? null}
                  onConfirmed={(task) => {
                    setConfirmed((prev) => ({ ...prev, [draft.sourceMessageId]: task.id }));
                    // Spoken only with the created task's id (A7).
                    controller.announce({ kind: "task", taskId: task.id });
                  }}
                  onAlreadyConfirmed={() => dismissTask(draft.sourceMessageId)}
                  onDiscard={() => dismissTask(draft.sourceMessageId)}
                />
              ))
            : null}
          {workspaceId
            ? Object.values(toolDrafts).map((live) => (
                <ToolRequestCard
                  key={live.draft.sourceMessageId}
                  draft={live.draft}
                  workspaceId={workspaceId}
                  created={live.created}
                  onCreated={(created) => {
                    setToolDrafts((prev) => ({
                      ...prev,
                      [live.draft.sourceMessageId]: { draft: live.draft, created },
                    }));
                    controller.announce({ kind: "approval_request", approvalId: created.id });
                  }}
                  onDiscard={() => dismissTool(live.draft.sourceMessageId)}
                />
              ))
            : null}
        </section>
      ) : null}

      {proposals.length > 0 ? (
        <ul className="flex w-full max-w-xl flex-col gap-1 text-xs text-muted-foreground" data-testid="voice-proposals">
          {proposals.map((memory) => (
            <li key={memory.id}>
              <Link href="/memory" className="underline underline-offset-2">
                {memory.title}
              </Link>{" "}
              — {tr.voice.memoryProposed}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="flex flex-wrap items-center justify-center gap-3">
        <Button
          type="button"
          size="lg"
          variant={micOpen ? "default" : "outline"}
          className="size-16 rounded-full"
          aria-pressed={micOpen}
          aria-label={micOpen ? tr.voice.micStop : tr.voice.micStart}
          title={tr.voice.micHint}
          data-testid="voice-mic"
          disabled={mute.muted || !workspaceId}
          onPointerDown={handlePointerDown}
          onPointerUp={handlePointerUp}
          onPointerLeave={() => {
            if (pressStartedRef.current !== 0) handlePointerUp();
          }}
          onClick={handleClick}
        >
          <Mic className="size-6" aria-hidden />
        </Button>
        {busy || micOpen || phase === "finishing" ? (
          <Button type="button" variant="outline" onClick={() => controller.stop()}>
            <Square aria-hidden className="size-4" />
            {tr.voice.stop}
          </Button>
        ) : null}
      </div>

      {full ? (
        <>
      <WakeSettingsPanel disabled={mute.muted || !workspaceId} />

      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={preview}
          onChange={(e) => {
            setPreview(e.target.checked);
            savePreview(e.target.checked);
          }}
        />
        {tr.voice.previewToggle}
      </label>

      <div className="flex w-full max-w-xl items-end gap-2">
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Friday'ye yaz…"
          disabled={busy}
          rows={2}
          className="flex-1 resize-none"
        />
        <Button type="button" onClick={() => void handleTypedSend()} disabled={busy || !value.trim()}>
          Gönder
        </Button>
      </div>
        </>
      ) : null}
    </div>
  );
}
