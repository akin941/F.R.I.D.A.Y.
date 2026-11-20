/**
 * A minimal ONNX-session surface (Phase 6 branch 4), so the openWakeWord and
 * Silero pipelines are pure TypeScript over an injected session: vitest
 * drives them with fake sessions, the Worker adapts `onnxruntime-web`'s
 * `InferenceSession` to this shape. No test loads a real model.
 */

export type TensorData = Float32Array | BigInt64Array;

export interface TensorLike {
  type: "float32" | "int64";
  data: TensorData;
  dims: readonly number[];
}

export interface SessionOutput {
  data: ArrayLike<number> | Float32Array;
  dims: readonly number[];
}

export interface SessionLike {
  readonly inputNames: readonly string[];
  readonly outputNames: readonly string[];
  run(feeds: Record<string, TensorLike>): Promise<Record<string, SessionOutput>>;
}

export function float32(data: Float32Array, dims: readonly number[]): TensorLike {
  return { type: "float32", data, dims };
}

export function int64(values: number[], dims: readonly number[]): TensorLike {
  return { type: "int64", data: BigInt64Array.from(values.map((v) => BigInt(v))), dims };
}

/** The first (or named) output of a session run. */
export function firstOutput(
  session: SessionLike,
  outputs: Record<string, SessionOutput>,
  name?: string,
): SessionOutput {
  const key = name && name in outputs ? name : (session.outputNames[0] ?? Object.keys(outputs)[0]);
  const out = key ? outputs[key] : undefined;
  if (!out) throw new Error("session returned no output");
  return out;
}
