import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { SESSION_HUD_TOKENS } from "@/components/hud/HudLearningPanels";
import { HUD_STATES } from "@/lib/hud/states";
import { STATUS_TONES, TONE_PILL_CLASSES, type StatusTone } from "@/lib/theme/status";

/**
 * WCAG 2.x AA contrast (4.5:1 for normal text) over every text/surface
 * token pair the UI actually renders, in BOTH palettes of globals.css.
 * Colors are oklch(); converted oklch -> OKLab -> linear sRGB (clamped to
 * the sRGB gamut) -> relative luminance.
 */

const CSS = readFileSync(path.resolve(__dirname, "../../app/globals.css"), "utf8");

function block(selector: string): Record<string, string> {
  const start = CSS.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`no ${selector} block`);
  const body = CSS.slice(start, CSS.indexOf("}", start));
  const vars: Record<string, string> = {};
  for (const match of body.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) {
    vars[match[1]!] = match[2]!.trim();
  }
  return vars;
}

const LIGHT = block(":root");
const DARK = { ...LIGHT, ...block(".dark") };

function resolve(palette: Record<string, string>, name: string, depth = 0): string {
  const value = palette[name];
  if (value === undefined) throw new Error(`--${name} is not defined`);
  const ref = value.match(/^var\(--([a-z0-9-]+)\)$/);
  if (ref) {
    if (depth > 5) throw new Error(`--${name}: var() cycle`);
    return resolve(palette, ref[1]!, depth + 1);
  }
  return value;
}

function luminance(oklch: string): number {
  const match = oklch.match(/^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\)$/);
  if (!match) throw new Error(`not an opaque oklch() color: ${oklch}`);
  const [L, C, H] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const h = (H * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const clamp = (x: number) => Math.min(1, Math.max(0, x));
  const r = clamp(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s);
  const g = clamp(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s);
  const bl = clamp(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s);
  return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
}

function contrast(palette: Record<string, string>, fg: string, bg: string): number {
  const a = luminance(resolve(palette, fg));
  const b = luminance(resolve(palette, bg));
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

const SEMANTIC = ["success", "warning", "info", "danger"] as const;

// [text token, surface token]
const PAIRS: [string, string][] = [
  ["foreground", "background"],
  ["card-foreground", "card"],
  ["popover-foreground", "popover"],
  ["primary-foreground", "primary"],
  ["primary", "background"],
  ["secondary-foreground", "secondary"],
  ["muted-foreground", "background"],
  ["muted-foreground", "card"],
  ["muted-foreground", "muted"],
  ["accent-foreground", "accent"],
  ["destructive", "background"],
  ["destructive", "card"],
  ["sidebar-foreground", "sidebar"],
  ["sidebar-primary-foreground", "sidebar-primary"],
  ["sidebar-accent-foreground", "sidebar-accent"],
  // Phase 6 A8: HUD captions, and every HUD state colour as text.
  ["hud-caption", "hud-surface"],
  ["hud-caption-muted", "hud-surface"],
  ...HUD_STATES.map((state): [string, string] => [`hud-${state}`, "hud-surface"]),
  ...SEMANTIC.flatMap((tone): [string, string][] => [
    [`${tone}-foreground`, tone],
    [tone, `${tone}-muted`],
    [tone, "background"],
    [tone, "card"],
  ]),
];

describe.each([
  ["light", LIGHT],
  ["dark", DARK],
])("%s palette", (_name, palette) => {
  it.each(PAIRS)("--%s on --%s meets WCAG AA (4.5:1)", (fg, bg) => {
    expect(contrast(palette, fg, bg)).toBeGreaterThanOrEqual(4.5);
  });

  it("uses one brand accent for --primary and --ring", () => {
    expect(resolve(palette, "ring")).toBe(resolve(palette, "primary"));
    expect(resolve(palette, "primary")).not.toMatch(/oklch\([\d.]+ 0 0\)/);
  });
});

describe("status tone map", () => {
  it("uses a checked pill pairing for every tone", () => {
    const tones = new Set<StatusTone>(
      Object.values(STATUS_TONES).flatMap((map) => Object.values(map) as StatusTone[]),
    );
    for (const tone of tones) {
      const classes = TONE_PILL_CLASSES[tone];
      if (tone === "neutral") {
        expect(classes).toContain("bg-muted");
        expect(classes).toContain("text-muted-foreground");
      } else {
        expect(classes).toContain(`bg-${tone}-muted`);
        expect(classes).toContain(`text-${tone}`);
      }
    }
  });

  it("colours the HUD sessions panel only with checked HUD text tokens (Phase 8A)", () => {
    const checked = new Set(PAIRS.filter(([, bg]) => bg === "hud-surface").map(([fg]) => fg));
    for (const className of Object.values(SESSION_HUD_TOKENS)) {
      expect(className.startsWith("text-")).toBe(true);
      expect(checked.has(className.slice("text-".length))).toBe(true);
    }
  });

  it("maps every coding-session status to a tone (Phase 8A)", () => {
    expect(Object.keys(STATUS_TONES.codingSession).sort()).toEqual(
      ["running", "stale", "stopped", "waiting_for_owner"],
    );
  });

  it("covers the 8 implementation pipeline statuses", () => {
    expect(Object.keys(STATUS_TONES.implementation)).toHaveLength(8);
  });
});

describe("Phase 8B branch 9 surfaces", () => {
  it("add no colour token: the graph, timeline and eval views use only checked theme tokens", () => {
    const dir = path.resolve(__dirname, "../../components/memory");
    for (const file of ["GraphView.tsx", "EntityTimeline.tsx", "EvalRunsView.tsx"]) {
      const source = readFileSync(path.join(dir, file), "utf8");
      expect(source).not.toMatch(/#[0-9a-fA-F]{6}\b|oklch\(/);
      expect(source).not.toMatch(/\b(?:text|bg|border)-(?:red|green|blue|yellow|amber|emerald|slate|gray|zinc)-\d{2,3}\b/);
    }
  });
});
