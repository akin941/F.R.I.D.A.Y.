"use client";

import { useEffect, useRef } from "react";
import { FrameLoop, type FrameLoopDeps } from "@/lib/hud/frame-loop";
import { HUD_VISUALS, type HudState } from "@/lib/hud/states";

/**
 * The HUD's Canvas-2D orb (Phase 6 A8): the fallback when WebGL is
 * unavailable or motion is reduced, dependency-free like the /voice orb.
 * Driven by the HUD frame budget (`fps`): at 0 -- hidden tab, reduced
 * motion, battery rest -- it holds no animation frame and draws ONE static
 * frame per state change, so the state still shows as colour (and as the
 * text label next to it).
 */
export interface HudCanvasOrbProps {
  state: HudState;
  /** Frames per second allowed right now (0 = one static frame). */
  fps: number;
  level?: () => number;
  size?: number;
  /** Test seam: replace requestAnimationFrame. */
  frameDeps?: FrameLoopDeps;
}

function tokenColor(state: HudState): string {
  const visual = HUD_VISUALS[state];
  if (typeof window === "undefined") return visual.glColors[1];
  const value = getComputedStyle(document.documentElement).getPropertyValue(visual.token).trim();
  return value || visual.glColors[1];
}

export function HudCanvasOrb({ state, fps, level, size = 260, frameDeps }: HudCanvasOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  const fpsRef = useRef(fps);
  const levelRef = useRef(level);
  const drawRef = useRef<((now: number) => void) | null>(null);
  const loopRef = useRef<FrameLoop | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d") ?? null;
    if (!canvas || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);
    const start = performance.now();
    let smoothed = 0;

    function blob(radius: number, color: string, alpha: number) {
      if (!ctx) return;
      const c = size / 2;
      const gradient = ctx.createRadialGradient(c, c, 0, c, c, radius);
      gradient.addColorStop(0, color);
      gradient.addColorStop(1, "transparent");
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      ctx.arc(c, c, radius, 0, Math.PI * 2);
      ctx.fillStyle = gradient;
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    function arc(radius: number, from: number, span: number, color: string, width = 3) {
      if (!ctx) return;
      const c = size / 2;
      ctx.beginPath();
      ctx.arc(c, c, radius, from, from + span);
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.lineCap = "round";
      ctx.stroke();
    }

    function draw(now: number) {
      if (!ctx) return;
      const current = stateRef.current;
      const visual = HUD_VISUALS[current];
      const animated = fpsRef.current > 0;
      const t = animated ? ((now - start) / 1000) * Math.max(visual.speed, 0.1) : 0;
      const raw = Math.max(0, Math.min(1, levelRef.current?.() ?? 0));
      smoothed = animated ? smoothed * 0.7 + raw * 0.3 : raw;
      const color = tokenColor(current);
      const r = size * 0.3;

      ctx.clearRect(0, 0, size, size);
      // The state ring: always drawn, so a static frame still shows colour.
      arc(size * 0.36, 0, Math.PI * 2, color, current === "muted" ? 1 : 2);

      switch (visual.pattern) {
        case "breathe": {
          const pulse = 0.5 + 0.5 * Math.sin(t * 1.4);
          blob(r * (0.78 + 0.1 * pulse), color, 0.35 + 0.2 * pulse);
          break;
        }
        case "level":
          blob(r * (0.7 + 0.45 * smoothed), color, current === "speaking" ? 0.65 : 0.5);
          break;
        case "spin":
          blob(r * 0.85, color, 0.3);
          arc(size * 0.3, t * 3, Math.PI * 0.6, color);
          break;
        case "orbit":
          blob(r * 0.8, color, 0.3);
          arc(size * 0.3, t * 4, Math.PI * 0.3, color);
          arc(size * 0.3, t * 4 + Math.PI, Math.PI * 0.3, color);
          break;
        case "pulse": {
          const beat = animated ? (Math.sin(t * 2.4) > 0 ? 1 : 0.6) : 1;
          blob(r * 0.85, color, 0.45 * beat);
          arc(size * 0.4, -Math.PI / 2, Math.PI * 2 * 0.75, color, 2);
          break;
        }
        case "flicker": {
          const on = animated ? 0.35 + 0.15 * Math.abs(Math.sin(t * 5)) : 0.45;
          blob(r * 0.8, color, on);
          arc(size * 0.3, Math.PI * 0.25, Math.PI * 0.5, color, 2);
          arc(size * 0.3, Math.PI * 1.25, Math.PI * 0.5, color, 2);
          break;
        }
        case "still":
          blob(r * 0.7, color, 0.25);
          break;
      }
    }

    drawRef.current = draw;
    const loop = new FrameLoop(draw, () => fpsRef.current, frameDeps);
    loopRef.current = loop;
    draw(performance.now());
    loop.refresh();
    return () => {
      loop.stop();
      loopRef.current = null;
      drawRef.current = null;
    };
  }, [size, frameDeps]);

  useEffect(() => {
    stateRef.current = state;
    fpsRef.current = fps;
    levelRef.current = level;
    // One frame now (the static frame when fps is 0), then the budget.
    drawRef.current?.(performance.now());
    loopRef.current?.refresh();
  }, [state, fps, level]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      data-testid="hud-orb-canvas"
      data-state={state}
      data-fps={fps}
      style={{ width: size, height: size }}
    />
  );
}
