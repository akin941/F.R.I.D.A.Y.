#!/usr/bin/env node
/**
 * Phase 6 A8 build-manifest check: after `next build`, the three.js / R3F
 * code must live only in chunks that the /hud route alone can reach --
 * never in any other route's entry chunks, and not even in /hud's own
 * entry chunks (it is loaded with a dynamic import, after first paint).
 *
 *   npm run build && npm run check:hud-bundle
 *
 * How: every route's `*_client-reference-manifest.js` lists the chunks it
 * loads; chunk files reference the chunks they load lazily by file name.
 * A chunk containing the three.js renderer (or the HUD shader) is a "GL
 * chunk"; the check walks each route's chunks plus everything they
 * reference and fails if a non-HUD route reaches a GL chunk.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const NEXT = path.join(ROOT, ".next");
const CHUNKS = path.join(NEXT, "static", "chunks");
const HUD_ROUTE = "/(shell)/hud/page";
// three.js's renderer class name survives minification (it is a string in
// error messages); the shader uniform names are the HUD orb's own.
const GL_MARKERS = ["WebGLRenderer", "uOutputVolume"];

function fail(message) {
  console.error(`check-hud-bundle: ${message}`);
  process.exit(1);
}

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

if (!existsSync(CHUNKS)) fail("no .next/static/chunks -- run `npm run build` first");

const chunkFiles = walk(CHUNKS).filter((f) => f.endsWith(".js"));
const chunkText = new Map(chunkFiles.map((f) => [path.basename(f), readFileSync(f, "utf8")]));
const glChunks = [...chunkText].filter(([, text]) => GL_MARKERS.some((m) => text.includes(m))).map(([n]) => n);
if (glChunks.length === 0) fail("no three.js chunk found -- did the HUD orb stop being built?");

function references(name) {
  const text = chunkText.get(name) ?? "";
  return [...chunkText.keys()].filter((other) => other !== name && text.includes(other));
}

function closure(start) {
  const seen = new Set();
  const stack = [...start];
  while (stack.length) {
    const name = stack.pop();
    if (seen.has(name)) continue;
    seen.add(name);
    stack.push(...references(name));
  }
  return seen;
}

function routeManifests() {
  const manifests = {};
  for (const file of walk(path.join(NEXT, "server", "app")).filter((f) =>
    f.endsWith("_client-reference-manifest.js"),
  )) {
    const sandbox = { globalThis: {} };
    sandbox.globalThis = sandbox;
    vm.runInNewContext(readFileSync(file, "utf8"), sandbox);
    Object.assign(manifests, sandbox.__RSC_MANIFEST ?? {});
  }
  return manifests;
}

function entryChunks(manifest) {
  const names = new Set();
  for (const mod of Object.values(manifest.clientModules ?? {})) {
    for (const chunk of mod.chunks ?? []) names.add(path.basename(chunk));
  }
  for (const list of Object.values(manifest.entryJSFiles ?? {})) {
    for (const chunk of list) names.add(path.basename(chunk));
  }
  return names;
}

const manifests = routeManifests();
if (!manifests[HUD_ROUTE]) fail(`no manifest for ${HUD_ROUTE}`);

const problems = [];
for (const [route, manifest] of Object.entries(manifests)) {
  const entries = entryChunks(manifest);
  for (const gl of glChunks) {
    if (entries.has(gl)) problems.push(`${route} loads the three.js chunk ${gl} eagerly`);
  }
  if (route === HUD_ROUTE) {
    const reach = closure(entries);
    if (!glChunks.some((gl) => reach.has(gl))) problems.push(`${HUD_ROUTE} cannot reach the three.js chunk`);
    continue;
  }
  const reach = closure(entries);
  for (const gl of glChunks) {
    if (reach.has(gl)) problems.push(`${route} can reach the three.js chunk ${gl}`);
  }
}

if (problems.length) fail(`\n  ${problems.join("\n  ")}`);
console.log(
  `check-hud-bundle: ok -- ${glChunks.length} three.js chunk(s), reachable from ${HUD_ROUTE} only (lazily), ${Object.keys(manifests).length} routes checked`,
);
