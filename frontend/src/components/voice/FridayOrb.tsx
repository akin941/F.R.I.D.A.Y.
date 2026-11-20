"use client";

import { useEffect, useRef } from "react";

export type FridayOrbState = "idle" | "thinking" | "speaking";

interface FridayOrbProps {
  state: FridayOrbState;
  size?: number;
}

/**
 * Dependency-free Canvas-2D animation (no framer-motion/three.js — none
 * exist in this frontend and this component doesn't introduce them). Colors
 * are read once from the active theme's CSS custom properties so the orb
 * stays in sync with globals.css without hardcoding a palette here.
 */
export function FridayOrb({ state, size = 220 }: FridayOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef<FridayOrbState>(state);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

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

    let frameId: number;
    const start = performance.now();

    function draw(now: number) {
      if (!ctx) return;
      const t = (now - start) / 1000;
      const cx = size / 2;
      const cy = size / 2;
      const current = stateRef.current;

      ctx.clearRect(0, 0, size, size);

      // Idle ring — always present, the orb's "resting" outline.
      ctx.beginPath();
      ctx.arc(cx, cy, size * 0.32, 0, Math.PI * 2);
      ctx.strokeStyle = ringColor;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      if (current === "idle") {
        const pulse = 0.5 + 0.5 * Math.sin(t * 1.2);
        const radius = size * (0.22 + 0.03 * pulse);
        const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
        gradient.addColorStop(0, idleColor);
        gradient.addColorStop(1, "transparent");
        ctx.globalAlpha = 0.35 + 0.15 * pulse;
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();
        ctx.globalAlpha = 1;
      } else if (current === "thinking") {
        const radius = size * 0.26;
        const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
        gradient.addColorStop(0, accent);
        gradient.addColorStop(1, "transparent");
        ctx.globalAlpha = 0.3;
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();
        ctx.globalAlpha = 1;
        // Rotating arc — the "working on it" tell.
        ctx.beginPath();
        ctx.arc(cx, cy, size * 0.28, t * 3, t * 3 + Math.PI * 0.6);
        ctx.strokeStyle = accent;
        ctx.lineWidth = 3;
        ctx.lineCap = "round";
        ctx.stroke();
      } else {
        // speaking — amplitude-style jitter from a deterministic
        // sum-of-sines fake waveform (no real audio wired up yet).
        const amplitude =
          0.5 +
          0.25 * Math.sin(t * 9) +
          0.15 * Math.sin(t * 17 + 1) +
          0.1 * Math.sin(t * 29 + 2);
        const radius = size * (0.22 + 0.09 * Math.max(0, amplitude));
        const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
        gradient.addColorStop(0, accent);
        gradient.addColorStop(1, "transparent");
        ctx.globalAlpha = 0.55;
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      frameId = requestAnimationFrame(draw);
    }

    frameId = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frameId);
  }, [size]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: size, height: size }}
      role="img"
      aria-label={
        state === "idle" ? "Friday boşta" : state === "thinking" ? "Friday düşünüyor" : "Friday konuşuyor"
      }
    />
  );
}
