import { test, expect } from "@playwright/test";
import {
  enroll,
  holdSpace,
  installVoiceStubs,
  recordVoiceRequests,
  voiceProbe,
} from "./voice-helpers";

/**
 * Phase 6 closeout e2e (brief §6, CI, fake providers): the push-to-talk
 * loop end to end, and the HUD showing the voice state and a pending
 * approval deep link.
 *
 * Stubbed capture (voice-helpers.ts) -> /api/voice/transcriptions (fake
 * STT) -> transcript held for review ("Göndermeden önce göster") -> the
 * unchanged chat turn (`input_mode="voice"`) -> the FakeAdapter reply ->
 * /api/voice/speech once per sentence (fake TTS, a silent WAV) -> a task
 * draft card -> a CLICK confirms it (D8: never the voice) -> the task
 * exists.
 */

// backend/ai_core/adapters/fake.py
const FAKE_TRANSCRIPT = "Bu, sahte adaptörün ürettiği örnek bir çevrim yazısıdır.";
const FAKE_REPLY_SENTENCES: string[] = ["Merhaba, ben Friday.", "MVP 1 gateway üzerinden çalışıyorum."];
// The FakeAdapter's router trigger phrases (`_TASK_DRAFT_TRIGGER`,
// `_TOOL_REQUEST_TRIGGER`) and its fixed task draft title.
const TASK_TURN = "Remind me to call the dentist tomorrow";
const TOOL_TURN = "Please export my vault";
const FAKE_TASK_TITLE = "Fake extracted task";

test("push-to-talk turn, spoken reply per sentence, task draft confirmed by click, HUD", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await installVoiceStubs(page, { preview: true });
  const voiceRequests = recordVoiceRequests(page);
  await enroll(page, "e2e voice browser");

  await page.goto("/voice");
  const status = page.getByTestId("voice-status");
  await expect(status).toHaveText(/Boşluk tuşunu basılı tut/);
  // Nothing about the voice page opens the mic before a press (D6/D7).
  expect((await voiceProbe(page)).getUserMedia).toBe(0);
  expect(voiceRequests).toHaveLength(0);

  // --- Turn 1: hold Space, release, the transcript comes back for review.
  const review = page.getByTestId("voice-review");
  await expect(async () => {
    await holdSpace(page, 1_200);
    await expect(review).toBeVisible({ timeout: 10_000 });
  }).toPass({ timeout: 30_000 });
  expect((await voiceProbe(page)).getUserMedia).toBeGreaterThanOrEqual(1);
  const upload = voiceRequests.find((r) => r.url().includes("/api/voice/transcriptions"));
  expect(upload, "the utterance was uploaded once released").toBeTruthy();
  expect(upload!.headers()["x-friday-trace"]).toMatch(/^[0-9a-f-]{36}$/);
  await expect(review.getByRole("textbox")).toHaveValue(FAKE_TRANSCRIPT);

  const speechBefore = voiceRequests.filter((r) => r.url().includes("/api/voice/speech")).length;
  await review.getByRole("button", { name: "Gönder" }).click();
  await expect(page.getByTestId("voice-reply")).toContainText(FAKE_REPLY_SENTENCES.join(" "), {
    timeout: 20_000,
  });
  // One speech request per reply sentence (D7), in order.
  await expect
    .poll(
      () => voiceRequests.filter((r) => r.url().includes("/api/voice/speech")).length - speechBefore,
      { timeout: 15_000 },
    )
    .toBe(FAKE_REPLY_SENTENCES.length);
  const spoken = voiceRequests
    .filter((r) => r.url().includes("/api/voice/speech"))
    .slice(speechBefore)
    .map((r) => (r.postDataJSON() as { text: string }).text);
  expect(spoken).toEqual(FAKE_REPLY_SENTENCES);
  await expect.poll(async () => (await voiceProbe(page)).played, { timeout: 15_000 }).toBeGreaterThan(0);
  await expect(status).toHaveText(/Boşluk tuşunu basılı tut/, { timeout: 20_000 });

  // --- Turn 2: the owner corrects the transcript into a task request.
  await expect(async () => {
    await holdSpace(page, 1_200);
    await expect(review).toBeVisible({ timeout: 10_000 });
  }).toPass({ timeout: 30_000 });
  await review.getByRole("textbox").fill(TASK_TURN);
  await review.getByRole("button", { name: "Gönder" }).click();

  const confirm = page.getByRole("button", { name: "Görevi onayla" });
  await expect(confirm).toBeVisible({ timeout: 20_000 });
  // D8: the draft waits for a click; the voice turn created nothing.
  await expect(page.getByText("Görev oluşturuldu", { exact: true })).toHaveCount(0);
  await confirm.click();
  await expect(page.getByText("Görev oluşturuldu", { exact: true })).toBeVisible();
  const openTask = page.getByRole("link", { name: "Görevi aç" });
  await expect(openTask).toHaveAttribute("href", /\/tasks/);
  await openTask.click();
  await expect(page.getByText(FAKE_TASK_TITLE).first()).toBeVisible({ timeout: 15_000 });

  // --- Turn 3 (typed on the voice page): a tool request becomes a pending
  // approval only through the card's click.
  await page.goto("/voice");
  const composer = page.getByPlaceholder("Friday'ye yaz…");
  await composer.fill(TOOL_TURN);
  await composer.press("Enter");
  const requestConfirm = page.getByRole("button", { name: "İsteği onayla" });
  await expect(requestConfirm).toBeVisible({ timeout: 20_000 });
  const created = page.waitForResponse(
    (r) => r.url().includes("/api/approvals/requests") && r.request().method() === "POST",
  );
  await requestConfirm.click();
  const approval = (await (await created).json()) as { id: string; status: string };
  expect(approval.status).toBe("pending");

  // --- HUD: the voice state and the pending approval as a deep link.
  await page.goto("/hud");
  const hud = page.getByTestId("hud");
  // At rest with a pending approval the HUD shows awaiting-approval (A8).
  await expect(hud).toHaveAttribute("data-state", "awaiting-approval", { timeout: 15_000 });
  await expect(page.getByTestId("hud-state-label")).toContainText("Onay bekliyor");
  await expect(page.getByTestId("hud-state-announcement")).toContainText("Onay bekliyor");
  const link = page.locator(`[data-testid="hud-approval-link"][href="/approvals/${approval.id}"]`);
  await expect(link).toBeVisible({ timeout: 15_000 });
  // No decide control on the HUD (D8).
  await expect(page.getByTestId("hud").getByRole("button", { name: /Onayla|Reddet/ })).toHaveCount(0);

  // Holding Space on the HUD drives the same loop: the state follows.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.down("Space");
  await expect(hud).toHaveAttribute("data-state", "listening", { timeout: 10_000 });
  await expect(page.getByTestId("hud-state-label")).toContainText("Dinliyor");
  await page.keyboard.up("Space");
  await page.keyboard.press("Escape");
  await expect(hud).toHaveAttribute("data-state", "awaiting-approval", { timeout: 15_000 });

  await link.click();
  await expect(page).toHaveURL(new RegExp(`/approvals/${approval.id}$`));
});
