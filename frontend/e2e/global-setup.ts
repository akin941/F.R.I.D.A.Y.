import { mkdirSync } from "node:fs";
import { issuePairingCode } from "./manage";

const backendApiUrl = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8001/api/v1";
const healthUrl = `${backendApiUrl.replace(/\/api\/v1\/?$/, "")}/healthz`;

const RETRIES = 20;
const DELAY_MS = 1000;

/**
 * Playwright's webServer only manages the frontend. The backend (Postgres +
 * migrations + seed_ai_defaults + runserver) is a separate precondition —
 * `make up` locally, a dedicated step in CI — so a misconfigured/absent
 * backend must fail fast with a clear message instead of a confusing
 * navigation timeout inside the first test.
 *
 * The smoke suite's curated-memory step (MVP 2 §6) depends on the extraction
 * job dispatched by the first reply actually being processed: `make up`'s
 * worker container does this for real; a bare `runserver` fallback has no
 * worker, so it also needs `CELERY_TASK_ALWAYS_EAGER=true` (as CI sets) or
 * the memory step will just poll until it times out.
 *
 * The approvals + vault export step (MVP 5 §5) needs `OBSIDIAN_VAULT_PATH`
 * to exist as a real directory by the time the smoke test's approve call
 * runs -- tier-2 vault-root validation only runs at request time, not at
 * backend startup, so this can safely run before the health check below.
 * A local `make up` stack already has this directory (the Docker worker's
 * bind mount from branch 3); this is a no-op there since the var is unset
 * for the frontend process in that setup. CI sets the same fixed path for
 * both the backend and this process (see ci.yml), so this creates the one
 * directory the backend process needs but can't create at boot itself.
 *
 * Device trust (Phase 4 branch 2): there is no signup any more. Once the
 * backend is up, this issues a one-time pairing PIN for the owner account
 * (`manage.py issue_pairing_code --plain`, via E2E_MANAGE_CMD -- see
 * e2e/manage.ts) and hands it to the smoke test as E2E_PAIRING_CODE. The
 * owner must exist: CI runs `bootstrap_owner --email e2e@example.test
 * --if-absent`; a local stack already has its owner. Dev settings accept a
 * PIN without a trusted client IP (DEVICE_PAIRING_ALLOW_UNKNOWN_IP).
 */
export default async function globalSetup() {
  if (process.env.OBSIDIAN_VAULT_PATH) {
    mkdirSync(process.env.OBSIDIAN_VAULT_PATH, { recursive: true });
  }

  let healthy = false;
  for (let attempt = 1; attempt <= RETRIES && !healthy; attempt += 1) {
    try {
      const response = await fetch(healthUrl);
      healthy = response.ok;
    } catch {
      // backend not reachable yet
    }
    if (!healthy) await new Promise((resolve) => setTimeout(resolve, DELAY_MS));
  }
  if (healthy) {
    process.env.E2E_PAIRING_CODE = issuePairingCode();
    return;
  }
  throw new Error(
    `Backend not reachable at ${healthUrl} after ${RETRIES}s. ` +
      "Start it first (`make up`, or migrate + seed_ai_defaults + " +
      "CELERY_TASK_ALWAYS_EAGER=true runserver on :8001).",
  );
}
