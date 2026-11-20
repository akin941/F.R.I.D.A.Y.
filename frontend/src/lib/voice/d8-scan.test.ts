import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * D8 / ADR-015 scan (Phase 6 branch 3): voice never decides and never
 * confirms. No voice source file -- the loop, the voice routes, the view --
 * calls the task-confirm, approval-create or decision helpers, or maps a
 * transcript to a click. Drafts are confirmed only inside the chat cards
 * (components/chat/), by a pointer/keyboard click.
 */
const ROOT = path.resolve(__dirname, "../..");
const SCANNED = [path.join(ROOT, "lib/voice"), path.join(ROOT, "components/voice")];

const FORBIDDEN: [RegExp, string][] = [
  [/["'`]\/api\/tasks/, "task create/confirm endpoint"],
  [/["'`]\/api\/approvals/, "approval create/decision endpoint"],
  [/\/(approve|reject|decide|decision|cancel|confirm)["'`/?]/, "a decision/confirm route"],
  [/\/api\/memories\/[^"'`]*\//, "a memory transition (approve/reject) route"],
  [/\.click\(\)/, "a programmatic click"],
  [/new (Mouse|Pointer|Keyboard|Submit)Event\(/, "a synthetic input event"],
  [/\bhandleConfirm\b|\bdecideApproval\b|\bconfirmTask\b|\bcreateApprovalRequest\b/, "a decision helper"],
  [/\bcanDecide\s*=\s*\{?\s*true/, "a decide control"],
];

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return sources(full);
    if (!/\.(ts|tsx)$/.test(name) || /\.test\.(ts|tsx)$/.test(name)) return [];
    return [full];
  });
}

describe("D8 scan: voice never decides, confirms or clicks", () => {
  const files = SCANNED.flatMap(sources);

  it("scans the voice sources", () => {
    expect(files.length).toBeGreaterThanOrEqual(10);
    expect(files.some((f) => f.endsWith("routes.ts"))).toBe(true);
    expect(files.some((f) => f.endsWith("VoiceView.tsx"))).toBe(true);
  });

  it.each(SCANNED.flatMap(sources).map((f) => [path.relative(ROOT, f), f]))(
    "%s calls no decision/confirm path",
    (_name, file) => {
      const text = readFileSync(file, "utf8");
      for (const [pattern, what] of FORBIDDEN) {
        expect(pattern.test(text), `${what} in ${file}`).toBe(false);
      }
    },
  );

  it("the only memory write from voice is a proposal", () => {
    const api = readFileSync(path.join(ROOT, "lib/voice/api.ts"), "utf8");
    const writes = api.match(/apiPostJson<[^>]+>\(\s*"[^"]+"/g) ?? [];
    expect(writes).toEqual(['apiPostJson<ApiMemory>("/api/memories"']);
    expect(api).toMatch(/proposal: true/);
  });

  it("the voice routes module imports nothing that can act", () => {
    const routes = readFileSync(path.join(ROOT, "lib/voice/routes.ts"), "utf8");
    expect(routes).not.toMatch(/^import /m);
  });
});
