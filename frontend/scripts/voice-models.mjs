#!/usr/bin/env node
/**
 * `make voice-models` (Phase 6 branch 4, D6): fetch the wake-word and VAD
 * models named in src/lib/voice/model-manifest.json from their pinned
 * release URLs, verify each sha256, and write them to the gitignored
 * public/voice-models/ (served same-origin; no CDN at runtime). The
 * onnxruntime-web WASM runtime is copied from node_modules (npm lockfile
 * integrity) to public/voice-models/ort/.
 *
 * The keyword model ("hey_friday", owner-trained offline) has no download
 * URL: pass its local path in VOICE_WAKE_MODEL_SRC. If its sha256 is not
 * pinned in the manifest yet, the script prints the file's hash and exits
 * non-zero -- pin it in the manifest (a reviewed change), then rerun.
 *
 * Nothing here runs in CI; vitest imports the pure helpers only.
 */
import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");

export function sha256Hex(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

/** Throws unless `bytes` hash to the manifest's pinned value. */
export function verifyBytes(entry, bytes) {
  if (!/^[0-9a-f]{64}$/.test(entry.sha256 ?? "")) {
    throw new Error(
      `${entry.file}: sha256 not pinned in model-manifest.json (this file hashes to ${sha256Hex(bytes)})`,
    );
  }
  const actual = sha256Hex(bytes);
  if (actual !== entry.sha256) {
    throw new Error(`${entry.file}: sha256 mismatch (expected ${entry.sha256}, got ${actual})`);
  }
  return true;
}

/** Only https release URLs (never a page or a redirecting shortener). */
export function assertPinnedSource(entry) {
  if (!entry.source) return;
  const url = new URL(entry.source);
  if (url.protocol !== "https:") throw new Error(`${entry.file}: source must be https`);
}

async function readExisting(file) {
  try {
    return await readFile(file);
  } catch {
    return null;
  }
}

async function main() {
  const manifest = JSON.parse(await readFile(path.join(root, "src/lib/voice/model-manifest.json"), "utf8"));
  const outDir = path.join(root, "public", manifest.basePath.replace(/^\/|\/$/g, ""));
  await mkdir(outDir, { recursive: true });
  let failed = false;
  for (const entry of manifest.models) {
    const target = path.join(outDir, entry.file);
    try {
      const existing = await readExisting(target);
      if (existing && /^[0-9a-f]{64}$/.test(entry.sha256) && sha256Hex(existing) === entry.sha256) {
        console.log(`ok      ${entry.file} (already verified)`);
        continue;
      }
      let bytes;
      if (entry.source) {
        assertPinnedSource(entry);
        const response = await fetch(entry.source);
        if (!response.ok) throw new Error(`${entry.file}: download failed (${response.status})`);
        bytes = Buffer.from(await response.arrayBuffer());
      } else {
        const src = process.env.VOICE_WAKE_MODEL_SRC;
        if (!src) throw new Error(`${entry.file}: owner-provided; set VOICE_WAKE_MODEL_SRC=/path/to/${entry.file}`);
        bytes = await readFile(src);
      }
      verifyBytes(entry, bytes);
      await writeFile(target, bytes);
      console.log(`fetched ${entry.file} (sha256 verified)`);
    } catch (err) {
      failed = true;
      console.error(`FAILED  ${err instanceof Error ? err.message : err}`);
    }
  }
  const ortDir = path.join(outDir, manifest.runtime.dir);
  await mkdir(ortDir, { recursive: true });
  for (const file of manifest.runtime.files) {
    await copyFile(path.join(root, "node_modules", manifest.runtime.package, "dist", file), path.join(ortDir, file));
    console.log(`copied  ${manifest.runtime.dir}${file}`);
  }
  if (failed) {
    console.error("voice-models: some models are missing or unverified; wake mode will refuse to arm.");
    process.exit(1);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
