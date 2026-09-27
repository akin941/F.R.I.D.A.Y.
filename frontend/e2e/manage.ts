import { execSync } from "node:child_process";

/**
 * How the e2e run reaches the backend's `manage.py` (Phase 4 branch 2): the
 * owner's first device is enrolled with a code only the host shell can
 * issue. Defaults to a host-side run next to this checkout (CI); a `make
 * up` stack sets e.g.
 *   E2E_MANAGE_CMD="docker compose -f ../deploy/docker/compose.yml exec -T web uv run python manage.py"
 */
export const MANAGE_CMD =
  process.env.E2E_MANAGE_CMD ?? "uv run --directory ../backend python manage.py";

/** A fresh one-time 8-digit pairing PIN for the owner (`issue_pairing_code --plain`). */
export function issuePairingCode(): string {
  const output = execSync(`${MANAGE_CMD} issue_pairing_code --plain`, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "inherit"],
  });
  const code = output.trim().split("\n").at(-1)?.trim() ?? "";
  if (!/^\d{8}$/.test(code)) {
    throw new Error(
      `issue_pairing_code printed no PIN (got ${JSON.stringify(output)}). Is there an owner ` +
        "account? CI runs `manage.py bootstrap_owner --email e2e@example.test --if-absent`.",
    );
  }
  return code;
}

/**
 * A fresh OpenAI-facade personal access token (`kind=openai`) for the e2e
 * owner, bound to their personal workspace (`create_dev_pat`, DEBUG only).
 */
export function mintFacadeToken(email = "e2e@example.test"): string {
  const output = execSync(
    `${MANAGE_CMD} create_dev_pat ${email} "${email} — Personal" --kind openai`,
    { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] },
  );
  const token = output.trim().split("\n").at(-1)?.trim() ?? "";
  if (!token.startsWith("jpat_")) {
    throw new Error(`create_dev_pat printed no token (got ${JSON.stringify(output)}).`);
  }
  return token;
}
