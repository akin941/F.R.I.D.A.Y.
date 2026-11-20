"use client";

import { useEffect, useRef } from "react";
import { tr } from "@/lib/i18n/tr";

/**
 * D10 first bullet (Phase 6 branch 3): the orb on REAL signals. `listening`
 * is driven by the mic RMS from the capture worklet and `speaking` by the
 * TTS AnalyserNode, both read through `level()` each frame -- the synthetic
 * jitter is gone. `armed` is reserved for wake mode (branch 4).
 */
export type FridayOrbState =
  | "idle"
  | "armed"
  | "listening"
  | "transcribing"
  | "thinking"
  | "speaking"
  | "error";

interface FridayOrbProps {
  state: FridayOrbState;
  size?: number;
  /** Current audio level 0..1 (mic while listening, playback while speaking). */
  level?: () => number;
}

/**
 * Dependency-free Canvas-2D animation (no framer-motion/three.js). Colors
 * are read from the active theme's CSS custom properties. With
 * `prefers-reduced-motion` the orb draws one static frame per state.
 */
export function FridayOrb({ state, size = 220, level }: FridayOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef<FridayOrbState>(state);
  const levelRef = useRef<FridayOrbProps["level"]>(level);
  // Reduced motion draws one frame per state change instead of a loop.
  const redrawRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    stateRef.current = state;
    levelRef.current = level;
    redrawRef.current?.();
  }, [state, level]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);

    const styles = getComputedStyle(document.documentElement);
    const accent = styles.getPropertyValue("--sidebar-primary").trim() || "oklch(0.488 0.243 264.376)";
    const idleColor = styles.getPropertyValue("--muted-foreground").trim() || "oklch(0.708 0 0)";
    const ringColor = styles.getPropertyValue("--border").trim() || "oklch(1 0 0 / 10%)";
    const errorColor = styles.getPropertyValue("--destructive").trim() || "oklch(0.577 0.245 27.325)";
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

    let frameId = 0;
    let smoothed = 0;
    const start = performance.now();

    function blob(cx: number, cy: number, radius: number, color: string, alpha: number) {
      if (!ctx) return;
      const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      gradient.addColorStop(0, color);
      gradient.addColorStop(1, "transparent");
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fillStyle = gradient;
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    function arc(cx: number, cy: number, from: number, span: number, color: string) {
      if (!ctx) return;
      ctx.beginPath();
      ctx.arc(cx, cy, size * 0.28, from, from + span);
      ctx.strokeStyle = color;
      ctx.lineWidth = 3;
      ctx.lineCap = "round";
      ctx.stroke();
    }

    function draw(now: number) {
      if (!ctx) return;
      const t = reduced ? 0 : (now - start) / 1000;
      const cx = size / 2;
      const cy = size / 2;
      const current = stateRef.current;
      const raw = Math.max(0, Math.min(1, levelRef.current?.() ?? 0));
      smoothed = reduced ? raw : smoothed * 0.7 + raw * 0.3;

      ctx.clearRect(0, 0, size, size);
      ctx.beginPath();
      ctx.arc(cx, cy, size * 0.32, 0, Math.PI * 2);
      ctx.strokeStyle = current === "armed" ? accent : ringColor;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      switch (current) {
        case "idle":
        case "armed": {
          const pulse = 0.5 + 0.5 * Math.sin(t * 1.2);
          blob(cx, cy, size * (0.22 + 0.03 * pulse), current === "armed" ? accent : idleColor, 0.35 + 0.15 * pulse);
          break;
        }
        case "listening":
        case "speaking":
          // Real audio level: mic RMS or the playback analyser.
          blob(cx, cy, size * (0.2 + 0.12 * smoothed), accent, current === "speaking" ? 0.6 : 0.45);
          break;
        case "transcribing":
          blob(cx, cy, size * 0.24, accent, 0.25);
          arc(cx, cy, t * 1.5, Math.PI * 0.4, accent);
          break;
        case "thinking":
          blob(cx, cy, size * 0.26, accent, 0.3);
          arc(cx, cy, t * 3, Math.PI * 0.6, accent);
          break;
        case "error":
          blob(cx, cy, size * 0.24, errorColor, 0.5);
          break;
      }
      if (!reduced) frameId = requestAnimationFrame(draw);
    }

    redrawRef.current = reduced ? () => draw(performance.now()) : null;
    frameId = requestAnimationFrame(draw);
    return () => {
      redrawRef.current = null;
      cancelAnimationFrame(frameId);
    };
  }, [size]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: size, height: size }}
      role="img"
      aria-label={tr.voice.orb[state]}
      data-state={state}
    />
  );
}
