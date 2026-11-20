import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Phase 6 A8 static guards for the HUD:
 *
 * 1. D8 scan, extended: no HUD source decides, confirms, posts or clicks --
 *    approvals are deep links to /approvals/<id> only, and the HUD's only
 *    network read is the event stream.
 * 2. Bundle split: `three` / `@react-three/*` are imported by exactly one
 *    module (the WebGL orb), which is reachable from no route entry through
 *    static imports -- only through HudOrb's dynamic `import()`, so the
 *    three.js chunk is requested by /hud alone. `scripts/check-hud-bundle.mjs`
 *    checks the same on a real `next build` output.
 */

const SRC = path.resolve(__dirname, "../..");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return sources(full);
    if (!/\.(ts|tsx)$/.test(name) || /\.test\.(ts|tsx)$/.test(name)) return [];
    return [full];
  });
}

const HUD_FILES = [
  ...sources(path.join(SRC, "components/hud")),
  ...sources(path.join(SRC, "lib/hud")),
  path.join(SRC, "components/shell/HudMiniOrb.tsx"),
  path.join(SRC, "app/(shell)/hud/page.tsx"),
  path.join(SRC, "app/api/hud/events/route.ts"),
];

const FORBIDDEN: [RegExp, string][] = [
  [/\/(approve|reject|decide|decision|cancel|confirm)["'`/?]/, "a decision/confirm route"],
  [/["'`]\/api\/tasks/, "a task create/confirm endpoint"],
  [/["'`]\/api\/approvals/, "an approval API call (the HUD links, it does not call)"],
  [/\bapiPostJson\b|\bapiPatchJson\b|\bapiPutJson\b|\bapiDelete\b|\bapiPostForm\b/, "a write helper"],
  [/method:\s*["'`](POST|PUT|PATCH|DELETE)["'`]/i, "a write request"],
  [/\.click\(\)/, "a programmatic click"],
  [/new (Mouse|Pointer|Keyboard|Submit)Event\(/, "a synthetic input event"],
  [/\bhandleConfirm\b|\bdecideApproval\b|\bconfirmTask\b|\bcreateApprovalRequest\b|\bDecisionDialog\b/, "a decision helper"],
  [/\bcanDecide\b/, "a decide control"],
];

describe("D8 scan, extended to the HUD", () => {
  it("scans the HUD sources", () => {
    expect(HUD_FILES.length).toBeGreaterThanOrEqual(10);
    for (const file of HUD_FILES) expect(existsSync(file), file).toBe(true);
  });

  it.each(HUD_FILES.map((f) => [path.relative(SRC, f), f]))("%s decides nothing", (_name, file) => {
    const text = readFileSync(file, "utf8");
    for (const [pattern, what] of FORBIDDEN) {
      expect(pattern.test(text), `${what} in ${file}`).toBe(false);
    }
  });

  it("links approvals to the decision route", () => {
    const panels = readFileSync(path.join(SRC, "components/hud/HudPanels.tsx"), "utf8");
    expect(panels).toContain("href={`/approvals/${item.id}`}");
  });
});

// --- bundle split ---

const STATIC_IMPORT = /(?:^|\n)\s*(?:import|export)\s+(?:type\s+)?(?:[^"';]*?\sfrom\s+)?["']([^"']+)["']/g;
const DYNAMIC_IMPORT = /import\(\s*["']([^"']+)["']\s*\)/g;
const ALL = sources(SRC);

function resolveImport(from: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith("@/")) base = path.join(SRC, spec.slice(2));
  else if (spec.startsWith(".")) base = path.resolve(path.dirname(from), spec);
  else return null;
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, path.join(base, "index.ts"), path.join(base, "index.tsx")]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  return null;
}

function staticImports(file: string): string[] {
  const text = readFileSync(file, "utf8");
  return [...text.matchAll(STATIC_IMPORT)].map((m) => m[1]!);
}

const THREE = /^(three|three\/.*|@react-three\/.*)$/;
const GL_ORB = path.join(SRC, "components/hud/orb/HudOrbGL.tsx");

describe("three.js is code-split to /hud", () => {
  it("only the WebGL orb imports three / @react-three", () => {
    const importers = ALL.filter((file) => staticImports(file).some((spec) => THREE.test(spec)));
    expect(importers.map((f) => path.relative(SRC, f))).toEqual(["components/hud/orb/HudOrbGL.tsx"]);
  });

  it("the WebGL orb is reached only through a dynamic import from HudOrb", () => {
    const staticRefs = ALL.filter((file) =>
      staticImports(file).some((spec) => resolveImport(file, spec) === GL_ORB),
    );
    expect(staticRefs).toEqual([]);
    const dynamicRefs = ALL.filter((file) =>
      [...readFileSync(file, "utf8").matchAll(DYNAMIC_IMPORT)].some(
        (m) => resolveImport(file, m[1]!) === GL_ORB,
      ),
    );
    expect(dynamicRefs.map((f) => path.relative(SRC, f))).toEqual(["components/hud/HudOrb.tsx"]);
  });

  it("no route entry reaches three.js through static imports", () => {
    const entries = sources(path.join(SRC, "app")).filter((f) => /(page|layout|route)\.tsx?$/.test(f));
    expect(entries.length).toBeGreaterThan(10);
    for (const entry of entries) {
      const seen = new Set<string>();
      const stack = [entry];
      while (stack.length) {
        const file = stack.pop()!;
        if (seen.has(file)) continue;
        seen.add(file);
        for (const spec of staticImports(file)) {
          expect(THREE.test(spec), `${path.relative(SRC, entry)} -> ${path.relative(SRC, file)} -> ${spec}`).toBe(false);
          const next = resolveImport(file, spec);
          if (next) stack.push(next);
        }
      }
      expect(seen.has(GL_ORB), path.relative(SRC, entry)).toBe(false);
    }
  });
});
