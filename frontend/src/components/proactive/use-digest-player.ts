"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ackDigest } from "@/lib/api/proactive";
import { apiPostJson } from "@/lib/api-client";
import { speakDigest, type DigestSpeaker } from "@/lib/proactive/digest-speech";
import { synthesize as voiceSynthesize } from "@/lib/voice/api";
import { Speaker, type SpeakerOptions } from "@/lib/voice/playback";
import { newTraceId } from "@/lib/voice/trace";
import type { ApiConversation } from "@/types/api";

/**
 * The "Dinle" player: one `Speaker` (the single speak function) whose
 * synthesis goes through `POST /api/voice/speech` -- one call per
 * sentence, `voice.tts`, the audio budget. The speech endpoint needs a
 * conversation of the workspace (its budget session); one "Brifing"
 * conversation is created lazily on the first play, never before.
 */
export interface DigestPlayerDeps {
  /** One sentence -> WAV bytes (tests pass a fake). */
  synthesize?: (text: string, signal: AbortSignal) => Promise<ArrayBuffer>;
  createSpeaker?: (opts: SpeakerOptions) => DigestSpeaker;
  ack?: typeof ackDigest;
}

export function useDigestPlayer(workspaceId: string | null, deps: DigestPlayerDeps = {}) {
  const [playing, setPlaying] = useState(false);
  const conversationRef = useRef<{ workspace: string; id: string } | null>(null);
  const workspaceRef = useRef(workspaceId);
  const depsRef = useRef(deps);
  const speakerRef = useRef<DigestSpeaker | null>(null);

  useEffect(() => {
    workspaceRef.current = workspaceId;
    depsRef.current = deps;
  });

  const conversationId = useCallback(async (): Promise<string> => {
    const workspace = workspaceRef.current;
    if (!workspace) throw new Error("no workspace");
    if (conversationRef.current?.workspace === workspace) return conversationRef.current.id;
    const conversation = await apiPostJson<ApiConversation>("/api/conversations", {
      workspace,
      title: "Brifing",
    });
    conversationRef.current = { workspace, id: conversation.id };
    return conversation.id;
  }, []);

  const speaker = useCallback((): DigestSpeaker => {
    if (speakerRef.current) return speakerRef.current;
    const synthesize =
      depsRef.current.synthesize ??
      (async (text: string, signal: AbortSignal) =>
        voiceSynthesize(await conversationId(), text, newTraceId(), {}, signal));
    const opts: SpeakerOptions = {
      synthesize,
      onIdle: () => setPlaying(false),
      onError: () => undefined,
    };
    const created = depsRef.current.createSpeaker ? depsRef.current.createSpeaker(opts) : new Speaker(opts);
    speakerRef.current = created;
    return created;
  }, [conversationId]);

  useEffect(
    () => () => {
      speakerRef.current?.stop();
    },
    [],
  );

  const play = useCallback(
    (digest: { id: string; spoken_text: string }): number => {
      const queued = speakDigest(digest.spoken_text, speaker());
      if (queued > 0) {
        setPlaying(true);
        void (depsRef.current.ack ?? ackDigest)(digest.id, "spoken").catch(() => undefined);
      }
      return queued;
    },
    [speaker],
  );

  const stop = useCallback(() => {
    speakerRef.current?.stop();
    setPlaying(false);
  }, []);

  return { playing, play, stop };
}
