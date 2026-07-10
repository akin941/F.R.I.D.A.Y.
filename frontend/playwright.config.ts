import { defineConfig, devices } from "@playwright/test";

const PORT = 3001;
const baseURL = `http://localhost:${PORT}`;
const backendApiUrl = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8001/api/v1";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["html", { open: "never" }], ["list"]] : "list",
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      BACKEND_API_URL: backendApiUrl,
      APP_ORIGIN: baseURL,
    },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
