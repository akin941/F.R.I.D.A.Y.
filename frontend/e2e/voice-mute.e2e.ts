import { test, expect } from "@playwright/test";
import { enroll, holdSpace, installVoiceStubs, recordVoiceRequests, voiceProbe } from "./voice-helpers";

/**
 * "A `?mute=1` voice tab is silent" (Phase 6 A7) -- the browser half of
 * `manage.py preflight`'s `voice_mute` check (PREFLIGHT_VOICE_MUTE_CMD runs
 * this spec against the live stack) and part of the CI e2e suite.
 *
 * Silent means: pressing push-to-talk opens no microphone and uploads
 * nothing, and a reply (typed, since nothing can be heard) makes no speech
 * request and plays nothing (the mute flag is checked inside the capture
 * path and inside the single speak function, A6).
 */

// backend/ai_core/adapters/fake.py
const FAKE_REPLY = "Merhaba, ben Friday. MVP 1 gateway üzerinden çalışıyorum.";

test("a ?mute=1 voice tab opens no mic, uploads nothing and plays nothing", async ({ page }) => {
  test.setTimeout(90_000);
  await installVoiceStubs(page);
  const voiceRequests = recordVoiceRequests(page);
  await enroll(page, "e2e voice-mute browser");

  await page.goto("/voice?mute=1");
  await expect(page.getByTestId("voice-muted")).toBeVisible();

  // The mic button is disabled; holding Space (the keyboard path, which
  // reaches the capture path itself) still opens nothing.
  await expect(page.getByTestId("voice-mic")).toBeDisabled();
  await holdSpace(page, 800);
  await page.waitForTimeout(1_500);

  // A typed turn still gets its reply on screen -- but never spoken.
  const composer = page.getByPlaceholder("Friday'ye yaz…");
  await composer.fill("Hello, Friday");
  await composer.press("Enter");
  await expect(page.getByTestId("voice-reply")).toContainText(FAKE_REPLY, { timeout: 20_000 });
  await page.waitForTimeout(1_500);

  expect(voiceRequests.map((r) => new URL(r.url()).pathname)).toEqual([]);
  expect(await voiceProbe(page)).toEqual({ getUserMedia: 0, played: 0 });
});
