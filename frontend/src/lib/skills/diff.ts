/**
 * A line diff for the `/skills` version view (Phase 12 D20): what changes
 * between the version in use and the proposed one, so the owner reviews
 * the exact text they approve. Plain LCS over lines; skill files are small
 * (the body is bounded at 16 000 characters), and an input beyond
 * `MAX_DIFF_LINES` per side falls back to "all removed, all added".
 */

export type DiffOp = "same" | "add" | "remove";

export interface DiffLine {
  op: DiffOp;
  text: string;
}

export const MAX_DIFF_LINES = 2000;

function splitLines(text: string): string[] {
  if (!text) return [];
  return text.replace(/\r\n?/g, "\n").replace(/\n$/, "").split("\n");
}

export function diffLines(before: string, after: string): DiffLine[] {
  const a = splitLines(before);
  const b = splitLines(after);
  if (a.length > MAX_DIFF_LINES || b.length > MAX_DIFF_LINES) {
    return [
      ...a.map((text) => ({ op: "remove" as const, text })),
      ...b.map((text) => ({ op: "add" as const, text })),
    ];
  }
  const rows = a.length + 1;
  const cols = b.length + 1;
  const table = new Uint32Array(rows * cols);
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i * cols + j] =
        a[i] === b[j]
          ? table[(i + 1) * cols + j + 1]! + 1
          : Math.max(table[(i + 1) * cols + j]!, table[i * cols + j + 1]!);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ op: "same", text: a[i]! });
      i++;
      j++;
    } else if (table[(i + 1) * cols + j]! >= table[i * cols + j + 1]!) {
      out.push({ op: "remove", text: a[i]! });
      i++;
    } else {
      out.push({ op: "add", text: b[j]! });
      j++;
    }
  }
  while (i < a.length) out.push({ op: "remove", text: a[i++]! });
  while (j < b.length) out.push({ op: "add", text: b[j++]! });
  return out;
}
