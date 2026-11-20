"use client";

/**
 * The HUD's WebGL orb (Phase 6 A8): React Three Fiber + a GLSL fragment
 * shader. This module -- the only one that imports `three` and
 * `@react-three/fiber` -- is loaded with a dynamic `import()` from
 * `HudOrb.tsx` on the /hud route only (code-split; see
 * `hud-bundle.test.ts` and `scripts/check-hud-bundle.mjs`).
 *
 * Seeded from the ElevenLabs UI `Orb` component
 * (github.com/elevenlabs/ui, apps/www/registry/elevenlabs-ui/ui/orb.tsx),
 * MIT License, Copyright (c) 2025 Eleven Labs Inc. -- full notice in
 * `frontend/THIRD_PARTY_NOTICES.md`. Changes from the seed: the remote
 * Perlin texture (a CDN fetch) is replaced by in-shader value noise, so
 * nothing is loaded from another origin; `@react-three/drei` is dropped;
 * the agent states are replaced by the eleven HUD states (`uniformsFor`);
 * the render loop is `frameloop="never"`, advanced only by the HUD frame
 * budget (30 / 5 / 0 fps, hidden tab, reduced motion, battery).
 */

import { useEffect, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { FrameLoop } from "@/lib/hud/frame-loop";
import { uniformsFor, type HudState } from "@/lib/hud/states";

export interface HudOrbGLProps {
  state: HudState;
  fps: number;
  level?: () => number;
  size?: number;
  /** Called when the GL context cannot be created or is lost for good. */
  onFailure?: () => void;
}

export default function HudOrbGL({ state, fps, level, size = 260, onFailure }: HudOrbGLProps) {
  return (
    <div
      aria-hidden
      data-testid="hud-orb-gl"
      data-state={state}
      data-fps={fps}
      style={{ width: size, height: size }}
    >
      <Canvas
        frameloop="never"
        dpr={[1, 1.5]}
        gl={{ alpha: true, antialias: true, premultipliedAlpha: true, powerPreference: "low-power" }}
        onCreated={({ gl }) => {
          gl.domElement.addEventListener("webglcontextlost", (event) => {
            event.preventDefault();
            onFailure?.();
          });
        }}
      >
        <Scene state={state} level={level} />
        <Driver fps={fps} state={state} />
      </Canvas>
    </div>
  );
}

/** Advances the never-looping canvas at the HUD frame budget. */
function Driver({ fps, state }: { fps: number; state: HudState }) {
  const advance = useThree((s) => s.advance);
  const fpsRef = useRef(fps);
  const loopRef = useRef<FrameLoop | null>(null);

  useEffect(() => {
    const loop = new FrameLoop((now) => advance(now), () => fpsRef.current);
    loopRef.current = loop;
    advance(performance.now());
    loop.refresh();
    return () => loop.stop();
  }, [advance]);

  useEffect(() => {
    fpsRef.current = fps;
    // A static frame for a state change while the budget is 0.
    if (fps <= 0) advance(performance.now());
    loopRef.current?.refresh();
  }, [fps, state, advance]);

  return null;
}

function Scene({ state, level }: { state: HudState; level?: () => number }) {
  const meshRef = useRef<THREE.Mesh<THREE.CircleGeometry, THREE.ShaderMaterial>>(null);
  const stateRef = useRef(state);
  const levelRef = useRef(level);

  useEffect(() => {
    stateRef.current = state;
    levelRef.current = level;
  }, [state, level]);

  // Built once; later states lerp in useFrame. Mutated only through the
  // mesh's material (the seed's pattern), never during render.
  const [uniforms] = useState(() => createUniforms(state));

  useEffect(() => {
    const apply = () => {
      const material = meshRef.current?.material;
      if (material) {
        material.uniforms.uInverted!.value = document.documentElement.classList.contains("dark") ? 1 : 0;
      }
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  const [motion] = useState(createMotion);

  useFrame((_, rawDelta) => {
    const material = meshRef.current?.material;
    if (!material) return;
    stepUniforms(
      material.uniforms,
      motion,
      uniformsFor(stateRef.current, levelRef.current?.() ?? 0),
      Math.min(rawDelta, 0.25),
    );
  });

  return (
    <mesh ref={meshRef}>
      <circleGeometry args={[3.5, 64]} />
      <shaderMaterial
        uniforms={uniforms}
        fragmentShader={FRAGMENT_SHADER}
        vertexShader={VERTEX_SHADER}
        transparent
      />
    </mesh>
  );
}

interface Motion {
  speed: number;
  input: number;
  output: number;
  color1: THREE.Color;
  color2: THREE.Color;
}

function createMotion(): Motion {
  return { speed: 0.1, input: 0, output: 0, color1: new THREE.Color(), color2: new THREE.Color() };
}

/** One frame's easing toward the state's targets (the seed's smoothing). */
function stepUniforms(
  u: Record<string, THREE.IUniform>,
  motion: Motion,
  target: ReturnType<typeof uniformsFor>,
  delta: number,
) {
  u.uTime!.value += delta * 0.5 * Math.max(target.speed, 0.05);
  motion.input += (target.input - motion.input) * 0.2;
  motion.output += (target.output - motion.output) * 0.2;
  const targetSpeed = target.speed * (0.1 + (1 - Math.pow(motion.output - 1, 2)) * 0.9);
  motion.speed += (targetSpeed - motion.speed) * 0.12;
  u.uAnimation!.value += delta * motion.speed;
  u.uInputVolume!.value = motion.input;
  u.uOutputVolume!.value = motion.output;
  motion.color1.set(target.color1);
  motion.color2.set(target.color2);
  (u.uColor1!.value as THREE.Color).lerp(motion.color1, 0.15);
  (u.uColor2!.value as THREE.Color).lerp(motion.color2, 0.15);
}

function createUniforms(state: HudState): Record<string, THREE.IUniform> {
  const initial = uniformsFor(state);
  const offsets = new Float32Array(7);
  // Fixed seed: the same orb every load (mulberry32-style).
  let seed = 0x9e3779b9;
  for (let i = 0; i < 7; i += 1) {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    offsets[i] = (((t ^ (t >>> 14)) >>> 0) / 4294967296) * Math.PI * 2;
  }
  return {
    uColor1: new THREE.Uniform(new THREE.Color(initial.color1)),
    uColor2: new THREE.Uniform(new THREE.Color(initial.color2)),
    uOffsets: { value: offsets },
    uTime: new THREE.Uniform(0),
    uAnimation: new THREE.Uniform(0.1),
    uInverted: new THREE.Uniform(0),
    uInputVolume: new THREE.Uniform(0),
    uOutputVolume: new THREE.Uniform(0),
    uOpacity: new THREE.Uniform(1),
  };
}

const VERTEX_SHADER = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

// Adapted from the ElevenLabs UI Orb fragment shader (MIT): the Perlin
// texture lookups are replaced by value noise computed in the shader.
export const FRAGMENT_SHADER = /* glsl */ `
uniform float uTime;
uniform float uAnimation;
uniform float uInverted;
uniform float uOffsets[7];
uniform vec3 uColor1;
uniform vec3 uColor2;
uniform float uInputVolume;
uniform float uOutputVolume;
uniform float uOpacity;
varying vec2 vUv;

const float PI = 3.14159265358979323846;

vec2 hash2(vec2 p) {
  return fract(sin(vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)))) * 43758.5453);
}

float noise2D(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float n = mix(
    mix(dot(hash2(i), f), dot(hash2(i + vec2(1.0, 0.0)), f - vec2(1.0, 0.0)), u.x),
    mix(dot(hash2(i + vec2(0.0, 1.0)), f - vec2(0.0, 1.0)),
        dot(hash2(i + vec2(1.0, 1.0)), f - vec2(1.0, 1.0)), u.x),
    u.y);
  return 0.5 + 0.5 * n;
}

// Replaces the seed's 'texture(uPerlinTexture, ...)' lookups.
float perlin(vec2 p) {
  return 0.6 * noise2D(p * 4.0) + 0.4 * noise2D(p * 9.0 + 3.1);
}

bool drawOval(vec2 polarUv, float a, float b, bool reverseGradient, float softness, out vec4 color) {
  float oval = (polarUv.x * polarUv.x) / (a * a) + (polarUv.y * polarUv.y) / (b * b);
  float edge = smoothstep(1.0, 1.0 - softness, oval);
  if (edge > 0.0) {
    float gradient = reverseGradient ? (1.0 - (polarUv.x / a + 1.0) / 2.0) : ((polarUv.x / a + 1.0) / 2.0);
    gradient = mix(0.5, gradient, 0.1);
    color = vec4(vec3(gradient), 0.85 * edge);
    return true;
  }
  return false;
}

vec3 colorRamp(float g, vec3 c1, vec3 c2, vec3 c3, vec3 c4) {
  if (g < 0.33) return mix(c1, c2, g * 3.0);
  if (g < 0.66) return mix(c2, c3, (g - 0.33) * 3.0);
  return mix(c3, c4, (g - 0.66) * 3.0);
}

float ring(vec3 d, float time, float start, float width, float scale, float gain) {
  float n = mix(noise2D(vec2(d.x, time) * scale), noise2D(vec2(d.y, time) * scale), d.z);
  return start + (n - 0.5) * gain * width;
}

void main() {
  vec2 uv = vUv * 2.0 - 1.0;
  float radius = length(uv);
  float theta = atan(uv.y, uv.x);
  if (theta < 0.0) theta += 2.0 * PI;
  vec3 d = vec3(theta / (2.0 * PI), mod(theta / (2.0 * PI) + 0.5, 1.0) + 1.0, abs(theta / PI - 1.0));

  float flowNoise = mix(
    perlin(vec2(radius * 0.03 - uAnimation * 0.2, d.x / 2.0)),
    perlin(vec2(radius * 0.03 - uAnimation * 0.2, d.y / 2.0)),
    d.z) - 0.5;
  theta += flowNoise * mix(0.08, 0.25, uOutputVolume);

  vec4 color = vec4(1.0);
  for (int i = 0; i < 7; i++) {
    float center = float(i) * 0.5 * PI + 0.5 * sin(uTime / 20.0 + uOffsets[i]);
    float n = perlin(vec2(mod(center + uTime * 0.05, 1.0), 0.5));
    float a = 0.5 + n * 0.3;
    float b = n * mix(3.5, 2.5, uInputVolume);
    float distTheta = min(abs(theta - center), min(abs(theta + 2.0 * PI - center), abs(theta - 2.0 * PI - center)));
    vec4 ovalColor;
    if (drawOval(vec2(distTheta, radius), a, b, (i - (i / 2) * 2) == 1, 0.6, ovalColor)) {
      color.rgb = mix(color.rgb, ovalColor.rgb, ovalColor.a);
      color.a = max(color.a, ovalColor.a);
    }
  }

  float r1 = ring(d, uTime * 0.1, 1.0, 0.3, 5.0, 3.75);
  float r2 = ring(d, uTime * 0.1, 0.9, 0.2, 6.0, 5.0);
  float in1 = radius + uInputVolume * 0.2;
  float in2 = radius + uInputVolume * 0.15;
  float a1 = (in2 >= r1) ? mix(0.2, 0.6, uInputVolume) : 0.0;
  float a2 = smoothstep(r2 - 0.05, r2 + 0.05, in1) * mix(0.15, 0.45, uInputVolume);
  float ringAlpha = max(a1, a2);
  color.rgb = 1.0 - (1.0 - color.rgb) * (1.0 - vec3(1.0) * ringAlpha);

  float luminance = mix(color.r, 1.0 - color.r, uInverted);
  color.rgb = colorRamp(luminance, vec3(0.0), uColor1, uColor2, vec3(1.0));
  color.a *= uOpacity;
  gl_FragColor = color;
}
`;
