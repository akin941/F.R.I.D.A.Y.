"use client";

import { CAPTURE_PROCESSOR_NAME, CAPTURE_WORKLET_SOURCE } from "@/lib/voice/capture-worklet";
import { CaptureError, type CaptureEnvironment } from "@/lib/voice/capture";
import { VOICE_SAMPLE_RATE } from "@/lib/voice/config";
import { readMuteState } from "@/lib/voice/mute";

/**
 * The always-open microphone of wake mode (Phase 6 branch 4, D6): the same
 * capture worklet as push-to-talk (16 kHz mono), but streaming frames to a
 * callback instead of collecting an utterance. This module keeps no audio:
 * each frame is handed on (to the wake Worker) and dropped.
 *
 * The mute flag is checked before the mic opens and on every frame (A6): a
 * muted -- or unreadable -- state closes the stream and releases the track.
 */

export interface MicStream {
  close(): void;
  readonly closed: boolean;
}

export interface MicStreamCallbacks {
  onFrame(samples: Float32Array, rms: number): void;
  onMuted?: (reason: "muted" | "unreadable") => void;
  /** The track ended on its own (device unplugged, permission revoked). */
  onEnded?: () => void;
}

let workletUrl: string | null = null;
function streamWorkletUrl(): string {
  if (!workletUrl) {
    workletUrl = URL.createObjectURL(new Blob([CAPTURE_WORKLET_SOURCE], { type: "application/javascript" }));
  }
  return workletUrl;
}

function mapMediaError(err: unknown): CaptureError {
  const name = err instanceof DOMException || err instanceof Error ? err.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return new CaptureError("mic_denied");
  if (name === "NotFoundError" || name === "OverconstrainedError") return new CaptureError("mic_missing");
  return new CaptureError("mic_failed");
}

export async function openMicStream(
  callbacks: MicStreamCallbacks,
  env: CaptureEnvironment = {},
): Promise<MicStream> {
  const readMute = env.readMute ?? readMuteState;
  const mute = readMute();
  if (mute.muted) {
    callbacks.onMuted?.(mute.readable ? "muted" : "unreadable");
    throw new CaptureError("muted");
  }
  const getUserMedia =
    env.getUserMedia ??
    (typeof navigator !== "undefined" && navigator.mediaDevices?.getUserMedia
      ? (c: MediaStreamConstraints) => navigator.mediaDevices.getUserMedia(c)
      : null);
  if (!getUserMedia) throw new CaptureError("mic_insecure");

  let stream: MediaStream;
  try {
    stream = await getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    throw mapMediaError(err);
  }

  let context: AudioContext | null = null;
  let node: AudioWorkletNode | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    for (const track of stream.getTracks()) track.stop();
    try {
      source?.disconnect();
      node?.disconnect();
      if (node) node.port.onmessage = null;
    } catch {
      // Already disconnected.
    }
    void context?.close().catch(() => undefined);
  };

  try {
    context = env.createAudioContext ? env.createAudioContext() : new AudioContext();
    if (!env.createWorkletNode) await context.audioWorklet.addModule(streamWorkletUrl());
    node = env.createWorkletNode
      ? env.createWorkletNode(context)
      : new AudioWorkletNode(context, CAPTURE_PROCESSOR_NAME, {
          processorOptions: { targetRate: VOICE_SAMPLE_RATE, frameSize: 1280 },
        });
    source = context.createMediaStreamSource(stream);
    source.connect(node);
  } catch {
    close();
    throw new CaptureError("mic_failed");
  }

  for (const track of stream.getTracks()) {
    track.addEventListener?.("ended", () => {
      if (closed) return;
      close();
      callbacks.onEnded?.();
    });
  }

  node.port.onmessage = (event: MessageEvent<{ samples: Float32Array; rms: number }>) => {
    if (closed) return;
    const state = readMute();
    if (state.muted) {
      close();
      callbacks.onMuted?.(state.readable ? "muted" : "unreadable");
      return;
    }
    const data = event.data;
    if (data?.samples?.length) callbacks.onFrame(data.samples, typeof data.rms === "number" ? data.rms : 0);
  };

  return {
    close,
    get closed() {
      return closed;
    },
  };
}
