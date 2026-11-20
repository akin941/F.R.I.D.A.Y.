import { expect } from "@playwright/test";
import type { Page, Request } from "@playwright/test";
import { issuePairingCode } from "./manage";

/**
 * Shared pieces of the Phase 6 voice e2e specs (closeout, brief §6).
 *
 * No real microphone, speech model or LLM: the backend runs the FakeAdapter
 * (fake STT returns a fixed transcript, fake TTS a silent WAV), and the
 * page's microphone is a STUB -- `getUserMedia` is replaced before any page
 * script runs with a function that counts its calls and returns an
 * oscillator `MediaStream` (a tone, generated in the page). Playback starts
 * (`AudioBufferSourceNode.start`) are counted the same way, so a spec can
 * prove "nothing was played" without listening to anything.
 */

declare global {
  interface Window {
    __fridayVoiceProbe?: { getUserMedia: number; played: number };
  }
}

export interface InstallOptions {
  /** Pre-set "Göndermeden önce göster" (Q5): the transcript opens for editing. */
  preview?: boolean;
}

export async function installVoiceStubs(page: Page, options: InstallOptions = {}) {
  await page.addInitScript(({ preview }) => {
    const probe = { getUserMedia: 0, played: 0 };
    window.__fridayVoiceProbe = probe;
    if (preview) {
      try {
        window.localStorage.setItem("friday:voice:preview", "1");
      } catch {
        // storage blocked: the spec's own assertions will fail loudly
      }
    }
    const fakeGetUserMedia = async () => {
      probe.getUserMedia += 1;
      const context = new AudioContext({ sampleRate: 48000 });
      const oscillator = context.createOscillator();
      oscillator.frequency.value = 220;
      const destination = context.createMediaStreamDestination();
      oscillator.connect(destination);
      oscillator.start();
      return destination.stream;
    };
    if (navigator.mediaDevices) {
      Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
        configurable: true,
        value: fakeGetUserMedia,
      });
    }
    const start = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (...args: Parameters<typeof start>) {
      probe.played += 1;
      return start.apply(this, args);
    };
  }, { preview: options.preview ?? false });
}

export async function voiceProbe(page: Page) {
  return page.evaluate(() => window.__fridayVoiceProbe ?? { getUserMedia: -1, played: -1 });
}

/** Every request the page makes to a `/api/voice/*` BFF route. */
export function recordVoiceRequests(page: Page) {
  const requests: Request[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/voice/")) requests.push(request);
  });
  return requests;
}

/** Enroll this browser context as an owner device (see global-setup.ts). */
export async function enroll(page: Page, deviceName: string) {
  await page.goto("/enroll");
  await page.getByLabel("Eşleştirme kodu").fill(issuePairingCode());
  await page.getByLabel("Cihaz adı").fill(deviceName);
  await page.getByRole("button", { name: "Cihazı eşleştir" }).click();
  await expect(page).toHaveURL(/\/chat$/);
}

/** Push-to-talk by keyboard: Space held for `ms`, then released (D7). */
export async function holdSpace(page: Page, ms: number) {
  // Space on a focused button or text field is not push-to-talk.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.down("Space");
  await page.waitForTimeout(ms);
  await page.keyboard.up("Space");
}
