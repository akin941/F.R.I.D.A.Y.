/**
 * The always-on wake-word Worker (Phase 6 branch 4, D6). Started only by
 * `wake-mode.ts` after a user gesture and a verified model load.
 *
 * It receives the hash-verified model bytes from the page (it never fetches
 * a model itself), runs `onnxruntime-web`'s single-threaded SIMD WASM build
 * (`numThreads = 1`, no cross-origin isolation needed; the WASM runtime is
 * served same-origin from `/voice-models/ort/`), and feeds every 16 kHz frame
 * to `WakeEngine`. Pre-wake audio never leaves this Worker: the only audio it
 * posts back is an utterance that starts after a detection.
 *
 * Not unit-tested in jsdom (no Worker/WASM there); its logic lives in the
 * tested `WakeEngine`, `OpenWakeWordDetector` and `SileroVad`. The real-model
 * run is the nightly/manual pass (owner step).
 */
import { SileroVad } from "@/lib/voice/vad";
import { WakeEngine, type WakeEngineConfig, type WakeInbound, type WakeOutbound } from "@/lib/voice/wake-engine";
import { OpenWakeWordDetector } from "@/lib/voice/wakeword/openwakeword";
import type { SessionLike, SessionOutput, TensorLike } from "@/lib/voice/wakeword/tensors";
import type { ModelRole } from "@/lib/voice/model-manifest";

type InitMessage = {
  type: "init";
  models: Record<ModelRole, ArrayBuffer>;
  runtimePath: string;
  config: Partial<WakeEngineConfig>;
};

interface WorkerScope {
  postMessage(message: unknown, transfer?: Transferable[]): void;
  onmessage: ((event: MessageEvent<InitMessage | WakeInbound>) => void) | null;
}

const scope = self as unknown as WorkerScope;
let engine: WakeEngine | null = null;

function post(message: WakeOutbound) {
  if (message.type === "utterance") scope.postMessage(message, [message.pcm.buffer]);
  else scope.postMessage(message);
}

async function init(message: InitMessage) {
  const ort = await import("onnxruntime-web/wasm");
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.proxy = false;
  ort.env.wasm.wasmPaths = message.runtimePath;
  const create = async (bytes: ArrayBuffer): Promise<SessionLike> => {
    const session = await ort.InferenceSession.create(new Uint8Array(bytes), {
      executionProviders: ["wasm"],
    });
    return {
      inputNames: session.inputNames,
      outputNames: session.outputNames,
      async run(feeds: Record<string, TensorLike>) {
        const ortFeeds: Record<string, InstanceType<typeof ort.Tensor>> = {};
        for (const [name, t] of Object.entries(feeds)) {
          ortFeeds[name] =
            t.type === "int64"
              ? new ort.Tensor("int64", t.data as BigInt64Array, t.dims)
              : new ort.Tensor("float32", t.data as Float32Array, t.dims);
        }
        const out = await session.run(ortFeeds);
        const result: Record<string, SessionOutput> = {};
        for (const [name, tensor] of Object.entries(out)) {
          result[name] = { data: tensor.data as Float32Array, dims: tensor.dims };
        }
        return result;
      },
    };
  };
  const [melspectrogram, embedding, keyword, vad] = await Promise.all([
    create(message.models.melspectrogram),
    create(message.models.embedding),
    create(message.models.keyword),
    create(message.models.vad),
  ]);
  engine = new WakeEngine(
    new OpenWakeWordDetector({ melspectrogram, embedding, keyword }),
    new SileroVad(vad),
    post,
    message.config,
  );
  post({ type: "ready" });
}

scope.onmessage = (event: MessageEvent<InitMessage | WakeInbound>) => {
  const data = event.data;
  if (data.type === "init") {
    init(data).catch((err: unknown) =>
      post({ type: "error", message: err instanceof Error ? err.message : String(err) }),
    );
    return;
  }
  void engine?.handle(data);
};
