"use client";

import type { WakeOutbound } from "@/lib/voice/wake-engine";
import type { WakePort } from "@/lib/voice/wake-mode";

/** The real wake Worker behind the `WakePort` seam (module Worker, same origin). */
export function createBrowserWakePort(): WakePort {
  const worker = new Worker(new URL("./wakeword-worker.ts", import.meta.url), {
    type: "module",
    name: "friday-wakeword",
  });
  return {
    post(message, transfer) {
      worker.postMessage(message, transfer ?? []);
    },
    onMessage(handler) {
      worker.onmessage = (event: MessageEvent<WakeOutbound>) => handler(event.data);
      worker.onerror = (event) => {
        event.preventDefault();
        handler({ type: "error", message: "worker_error" });
      };
    },
    terminate() {
      worker.onmessage = null;
      worker.onerror = null;
      worker.terminate();
    },
  };
}
