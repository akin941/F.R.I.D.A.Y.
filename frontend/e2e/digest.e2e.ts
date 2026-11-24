import { execSync } from "node:child_process";
import { test, expect } from "@playwright/test";
import { MANAGE_CMD } from "./manage";
import { enroll, installVoiceStubs, recordVoiceRequests, voiceProbe } from "./voice-helpers";

/**
 * The Phase 12 branch 2 "Done when" in the browser: a task due today, the
 * owner's `morning_digest` schedule made on /today, one heartbeat tick at
 * the scheduled minute (`manage.py proactive_heartbeat --at`, what the beat
 * does), then /today shows the digest card and "Dinle" speaks it through
 * `/api/voice/speech` with the fake speech provider -- and the microphone
 * is never opened. Needs the live e2e stack (CI), like voice.e2e.ts.
 */

function todayLocalDate(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

test("a scheduled digest appears on /today and Dinle speaks it", async ({ page }) => {
  test.setTimeout(120_000);
  await installVoiceStubs(page);
  const voiceRequests = recordVoiceRequests(page);
  await enroll(page, "e2e digest browser");

  // Probe content: a task due today.
  await page.goto("/tasks");
  await page.getByRole("button", { name: "Yeni görev" }).click();
  await page.getByLabel("Başlık").fill("Brifing görevi");
  await page.getByLabel("Son tarih").fill(todayLocalDate());
  await page.getByRole("button", { name: "Görev oluştur" }).click();
  await expect(page.getByText("Brifing görevi", { exact: true })).toBeVisible({ timeout: 10_000 });

  // The owner makes the schedule on /today (never Friday).
  await page.goto("/today");
  const editor = page.getByTestId("schedule-editor");
  const enabled = editor.getByLabel("Açık").first();
  await expect(enabled).toBeEnabled({ timeout: 10_000 });
  await enabled.check();
  for (const day of ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]) {
    await editor.getByLabel(day, { exact: true }).check();
  }
  const saved = page.waitForResponse(
    (r) => r.url().includes("/api/proactive/schedules/morning_digest") && r.request().method() === "PUT",
  );
  await editor.getByTestId("save-digest").click();
  const schedule = (await (await saved).json()) as { next_run_at: string };
  // Only an ISO timestamp ever reaches the shell command below.
  expect(schedule.next_run_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$/);

  // One heartbeat tick at the scheduled minute.
  execSync(`${MANAGE_CMD} proactive_heartbeat --at ${schedule.next_run_at}`, {
    stdio: ["ignore", "pipe", "inherit"],
  });

  await page.reload();
  const card = page.getByTestId("digest-card");
  await expect(card).toBeVisible({ timeout: 10_000 });
  await expect(card.getByTestId("digest-text")).not.toBeEmpty();

  await card.getByTestId("digest-listen").click();
  await expect
    .poll(() => voiceRequests.filter((r) => new URL(r.url()).pathname === "/api/voice/speech").length, {
      timeout: 20_000,
    })
    .toBeGreaterThan(0);
  expect((await voiceProbe(page)).getUserMedia).toBe(0);
});
