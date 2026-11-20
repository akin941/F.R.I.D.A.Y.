"use client";

import {
  CAPTURE_PROCESSOR_NAME,
  CAPTURE_WORKLET_SOURCE,
} from "@/lib/voice/capture-worklet";
import { VOICE_MAX_UTTERANCE_SECONDS, VOICE_SAMPLE_RATE } from "@/lib/voice/config";
import { readMuteState } from "@/lib/voice/mute";
import { concatPcm, encodeWav, floatToPcm16 } from "@/lib/voice/wav";

/**
 * Push-to-talk microphone capture (Phase 6 branch 3, D3).
 *
 * Audio exists only in this tab's memory from `startCapture()` to `stop()`
 * / `cancel()`: nothing is uploaded here -- the caller sends the finished
 * WAV to `/api/voice/transcriptions`. The mic track is released on every
 * exit path (stop, cancel, cap reached, mute, mute unreadable, error).
 *
 * The mute flag is checked inside the capture path itself (A6): before the
 * mic opens and on every frame. Muted -- or a mute state that cannot be
 * read -- cancels the capture, discards the audio and releases the track.
 */

export type CaptureErrorCode = "mic_denied" | "mic_missing" | "mic_insecure" | "mic_failed" | "muted";

export class CaptureError extends Error {
  code: CaptureErrorCode;
  constructor(code: CaptureErrorCode) {
    super(code);
    this.name = "CaptureError";
    this.code = code;
  }
}

export interface CaptureSession {
  /** Stop and return the utterance as a 16 kHz mono PCM16 WAV (null if empty). */
  stop(): Promise<Uint8Array<ArrayBuffer> | null>;
  /** Stop and discard everything captured. */
  cancel(): void;
  readonly samples: number;
}

export interface CaptureCallbacks {
  /** Mic RMS per frame (orb `listening` level). */
  onLevel?: (level: number) => void;
  /** The utterance cap was reached; the session stopped collecting. */
  onCapReached?: () => void;
  /** Capture was cut by the mute flag (or an unreadable mute state). */
  onMuted?: (reason: "muted" | "unreadable") => void;
}

export interface CaptureEnvironment {
  getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  createAudioContext?: () => AudioContext;
  createWorkletNode?: (context: AudioContext) => AudioWorkletNode;
  readMute?: typeof readMuteState;
  maxSeconds?: number;
}

function defaultGetUserMedia(): ((c: MediaStreamConstraints) => Promise<MediaStream>) | null {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) return null;
  return (constraints) => navigator.mediaDevices.getUserMedia(constraints);
}

function mapMediaError(err: unknown): CaptureError {
  const name = err instanceof DOMException || err instanceof Error ? err.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return new CaptureError("mic_denied");
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return new CaptureError("mic_missing");
  }
  return new CaptureError("mic_failed");
}

let workletUrl: string | null = null;
function captureWorkletUrl(): string {
  if (!workletUrl) {
    workletUrl = URL.createObjectURL(
      new Blob([CAPTURE_WORKLET_SOURCE], { type: "application/javascript" }),
    );
  }
  return workletUrl;
}

export async function startCapture(
  callbacks: CaptureCallbacks = {},
  env: CaptureEnvironment = {},
): Promise<CaptureSession> {
  const readMute = env.readMute ?? readMuteState;
  const maxSamples = (env.maxSeconds ?? VOICE_MAX_UTTERANCE_SECONDS) * VOICE_SAMPLE_RATE;
  const mute = readMute();
  if (mute.muted) {
    callbacks.onMuted?.(mute.readable ? "muted" : "unreadable");
    throw new CaptureError("muted");
  }
  const getUserMedia = env.getUserMedia ?? defaultGetUserMedia();
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
  const chunks: Int16Array[] = [];
  let samples = 0;
  let closed = false;
  let collecting = true;
  let finalResolve: (() => void) | null = null;

  const release = () => {
    if (closed) return;
    closed = true;
    collecting = false;
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
    if (!env.createWorkletNode) await context.audioWorklet.addModule(captureWorkletUrl());
    node = env.createWorkletNode
      ? env.createWorkletNode(context)
      : new AudioWorkletNode(context, CAPTURE_PROCESSOR_NAME, {
          processorOptions: { targetRate: VOICE_SAMPLE_RATE },
        });
    source = context.createMediaStreamSource(stream);
    source.connect(node);
  } catch {
    release();
    throw new CaptureError("mic_failed");
  }

  node.port.onmessage = (event: MessageEvent<{ samples: Float32Array; rms: number; final?: boolean }>) => {
    // A6: mute is checked inside the capture path, on every frame.
    const state = readMute();
    if (state.muted) {
      chunks.length = 0;
      samples = 0;
      release();
      finalResolve?.();
      callbacks.onMuted?.(state.readable ? "muted" : "unreadable");
      return;
    }
    const data = event.data;
    if (collecting && data?.samples?.length) {
      const room = maxSamples - samples;
      const frame = data.samples.length > room ? data.samples.subarray(0, room) : data.samples;
      chunks.push(floatToPcm16(frame));
      samples += frame.length;
      callbacks.onLevel?.(typeof data.rms === "number" ? data.rms : 0);
      if (samples >= maxSamples) {
        collecting = false;
        callbacks.onCapReached?.();
      }
    }
    if (data?.final) finalResolve?.();
  };

  return {
    get samples() {
      return samples;
    },
    async stop() {
      if (!closed && node) {
        // Ask the worklet to flush its partial frame, then release.
        const flushed = new Promise<void>((resolve) => {
          finalResolve = resolve;
          setTimeout(resolve, 150);
        });
        node.port.postMessage("stop");
        await flushed;
      }
      release();
      if (samples === 0) return null;
      const wav = encodeWav(concatPcm(chunks));
      chunks.length = 0;
      return wav;
    },
    cancel() {
      chunks.length = 0;
      samples = 0;
      release();
      finalResolve?.();
    },
  };
}
