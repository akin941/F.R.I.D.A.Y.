"use client";

import { Component, useEffect, useState, type ReactNode } from "react";
import dynamic from "next/dynamic";
import { HudCanvasOrb } from "@/components/hud/HudCanvasOrb";
import type { HudState } from "@/lib/hud/states";

/**
 * Picks the HUD orb renderer (Phase 6 A8): the WebGL orb when WebGL is
 * available and motion is not reduced, the Canvas-2D orb otherwise -- and
 * whenever the GL module fails to load, throws, or loses its context.
 *
 * The GL module is reached ONLY through this dynamic `import()`, so the
 * three.js / R3F code lives in its own chunk that only /hud ever requests
 * (`hud-bundle.test.ts` guards the import graph).
 */
const HudOrbGL = dynamic(() => import("@/components/hud/orb/HudOrbGL"), {
  ssr: false,
  loading: () => null,
});

export interface HudOrbProps {
  state: HudState;
  fps: number;
  reducedMotion: boolean;
  level?: () => number;
  size?: number;
  /** Test seam: WebGL detection. */
  webglAvailable?: () => boolean;
  /**
   * Phase 11 D15: whose voice the state is -- the browser's, or the Mac
   * node's (`voice.state`) while the browser is idle.
   */
  source?: "browser" | "node";
}

export function detectWebGL(): boolean {
  if (typeof document === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

class GLBoundary extends Component<{ fallback: ReactNode; onError: () => void; children: ReactNode }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch() {
    this.props.onError();
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

export function HudOrb({
  state,
  fps,
  reducedMotion,
  level,
  size = 260,
  webglAvailable = detectWebGL,
  source = "browser",
}: HudOrbProps) {
  // Detected after mount: the server render (and the first client render)
  // is always the canvas orb, so hydration never mismatches.
  const [gl, setGl] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    /* eslint-disable-next-line react-hooks/set-state-in-effect -- one-time browser capability probe after mount */
    setGl(webglAvailable());
  }, [webglAvailable]);
  const fallback = <HudCanvasOrb state={state} fps={fps} level={level} size={size} />;
  if (!gl || failed || reducedMotion) {
    return (
      <div data-testid="hud-orb" data-renderer="canvas" data-state={state} data-source={source}>
        {fallback}
      </div>
    );
  }
  return (
    <div data-testid="hud-orb" data-renderer="webgl" data-state={state} data-source={source}>
      <GLBoundary fallback={fallback} onError={() => setFailed(true)}>
        <HudOrbGL state={state} fps={fps} level={level} size={size} onFailure={() => setFailed(true)} />
      </GLBoundary>
    </div>
  );
}
