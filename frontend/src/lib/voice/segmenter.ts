/**
 * Reply text -> speakable sentences (Phase 6 D7).
 *
 * The reply streams as Markdown chunks. `SentenceSegmenter` turns it into
 * plain sentences of 20-300 characters as soon as each one is complete, so
 * the first sentence can be synthesized while the rest still streams:
 *
 * - code blocks -> "Kod ekranda." (a half-streamed fence is held back until
 *   it closes, never read out);
 * - links -> their label, images dropped, inline code -> its text, emphasis
 *   and heading/list/quote markers stripped, bare URLs -> "bağlantı";
 * - tables -> "Tablo ekranda." once per table;
 * - boundaries at `.`/`!`/`?`/`…`/newline followed by whitespace -- never
 *   after a Turkish/English abbreviation ("Dr.", "vb.", "örn."), an initial
 *   ("A.") or inside a decimal ("3.5"), and never at the very end of the
 *   streamed text (the next chunk may continue it).
 */

export const MIN_SENTENCE_CHARS = 20;
export const MAX_SENTENCE_CHARS = 300;

export const CODE_PLACEHOLDER = "Kod ekranda.";
export const TABLE_PLACEHOLDER = "Tablo ekranda.";

const ABBREVIATIONS = new Set([
  "dr",
  "prof",
  "doç",
  "doc",
  "yrd",
  "av",
  "sn",
  "bkz",
  "örn",
  "orn",
  "vb",
  "vs",
  "yy",
  "no",
  "tel",
  "mah",
  "cad",
  "sok",
  "apt",
  "st",
  "mr",
  "mrs",
  "ms",
  "jr",
  "sr",
  "etc",
  "vol",
  "fig",
  "ör",
]);

/** One Markdown line -> speakable text (block markers + inline syntax). */
export function speakableLine(line: string): string {
  let text = line;
  text = text.replace(/^\s{0,3}#{1,6}\s+/, ""); // heading
  text = text.replace(/^\s*>\s?/, ""); // quote
  text = text.replace(/^\s*(?:[-*+]|\d{1,3}[.)])\s+/, ""); // list item
  text = text.replace(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/, ""); // rule
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, ""); // image
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1"); // link -> label
  text = text.replace(/`([^`]*)`/g, "$1"); // inline code
  text = text.replace(/(\*\*|__)(.+?)\1/g, "$2"); // bold
  text = text.replace(/(^|[\s(])[*_]([^*_\s][^*_]*?)[*_](?=[\s).,!?:;]|$)/g, "$1$2"); // italic
  text = text.replace(/~~(.+?)~~/g, "$1");
  text = text.replace(/<https?:\/\/[^>\s]+>|https?:\/\/\S+/g, "bağlantı");
  return text.replace(/\s+/g, " ").trim();
}

/** A whole Markdown reply -> one speakable string (non-streaming helper). */
export function toSpeakable(markdown: string): string {
  const segmenter = new SentenceSegmenter();
  return [...segmenter.push(markdown), ...segmenter.flush()].join(" ");
}

function isTableRow(line: string): boolean {
  return /^\s*\|/.test(line);
}

function isFence(line: string): boolean {
  return /^\s{0,3}(```|~~~)/.test(line);
}

/** Markdown inline syntax that is still open at the end of `text`. */
function hasOpenInline(text: string): boolean {
  if ((text.match(/`/g) ?? []).length % 2 === 1) return true;
  if ((text.match(/\*\*/g) ?? []).length % 2 === 1) return true;
  const opens = (text.match(/\[/g) ?? []).length;
  const closes = (text.match(/\]/g) ?? []).length;
  if (opens > closes) return true;
  // "[label](" without its closing paren.
  if (/\]\([^)]*$/.test(text)) return true;
  return false;
}

/** Index just after each sentence boundary in `text` (whitespace follows). */
function boundaries(text: string): number[] {
  const result: number[] = [];
  const re = /([.!?…]+)["')\]]*(?=\s)|\n/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const end = match.index + match[0].length;
    if (match[0] === "\n") {
      result.push(end);
      continue;
    }
    if (match[1] === ".") {
      const before = text.slice(0, match.index);
      const word = /([\p{L}]+)$/u.exec(before)?.[1] ?? "";
      if (word && ABBREVIATIONS.has(word.toLocaleLowerCase("tr"))) continue;
      // An initial ("A.") or an ordinal number ("3. madde").
      if (word.length === 1 && word === word.toLocaleUpperCase("tr")) continue;
      if (/\d$/.test(before) && /^\s+\p{Ll}/u.test(text.slice(end))) continue;
    }
    result.push(end);
  }
  return result;
}

/** Split an over-long sentence at the last comma/space before the cap. */
function splitLong(sentence: string): string[] {
  const out: string[] = [];
  let rest = sentence;
  while (rest.length > MAX_SENTENCE_CHARS) {
    const window = rest.slice(0, MAX_SENTENCE_CHARS);
    let cut = Math.max(window.lastIndexOf(", "), window.lastIndexOf("; "));
    if (cut < MIN_SENTENCE_CHARS) cut = window.lastIndexOf(" ");
    if (cut < MIN_SENTENCE_CHARS) cut = MAX_SENTENCE_CHARS - 1;
    out.push(rest.slice(0, cut + 1).trim());
    rest = rest.slice(cut + 1).trim();
  }
  if (rest) out.push(rest);
  return out;
}

export class SentenceSegmenter {
  /** Raw Markdown not yet converted. */
  private raw = "";
  /** Speakable text not yet emitted as a sentence. */
  private speak = "";
  private inFence = false;
  private lastWasTable = false;

  /** Feed one streamed chunk; returns the sentences it completed. */
  push(chunk: string): string[] {
    this.raw += chunk;
    this.drain(false);
    return this.take(false);
  }

  /** The stream ended: everything left, as sentences. */
  flush(): string[] {
    this.drain(true);
    return this.take(true);
  }

  private append(text: string) {
    if (!text) return;
    this.speak += (this.speak && !/\s$/.test(this.speak) ? " " : "") + text;
  }

  private placeholder(text: string) {
    // Its own sentence, separated by a newline boundary.
    this.append(`${text}\n`);
  }

  private drain(final: boolean) {
    while (this.raw.length > 0) {
      const newline = this.raw.indexOf("\n");
      if (newline === -1 && !final) {
        this.drainPartialLine();
        return;
      }
      const line = newline === -1 ? this.raw : this.raw.slice(0, newline);
      this.raw = newline === -1 ? "" : this.raw.slice(newline + 1);
      if (this.inFence) {
        if (isFence(line)) {
          this.inFence = false;
          this.placeholder(CODE_PLACEHOLDER);
        }
        continue;
      }
      if (isFence(line)) {
        this.inFence = true;
        this.lastWasTable = false;
        continue;
      }
      if (isTableRow(line)) {
        if (!this.lastWasTable) this.placeholder(TABLE_PLACEHOLDER);
        this.lastWasTable = true;
        continue;
      }
      this.lastWasTable = false;
      const text = speakableLine(line);
      if (text) this.append(`${text}\n`);
    }
    if (final && this.inFence) {
      // An unclosed fence at the end of the reply is still code.
      this.inFence = false;
      this.placeholder(CODE_PLACEHOLDER);
    }
  }

  /** The trailing line without its newline: emit what is surely complete. */
  private drainPartialLine() {
    if (this.inFence) return;
    const line = this.raw;
    // Could become a fence or a table row: wait for the newline.
    if (/^\s*[`~|]/.test(line) || /^\s*$/.test(line)) return;
    const cuts = boundaries(line).filter((cut) => !hasOpenInline(line.slice(0, cut)));
    const cut = cuts.at(-1);
    if (cut === undefined) return;
    const done = line.slice(0, cut);
    // Line-start markers ("- ", "## ") belong to the first piece only.
    const text = speakableLine(done);
    this.raw = line.slice(cut).replace(/^\s+/, "");
    if (text) this.append(`${text} `);
  }

  private take(final: boolean): string[] {
    const out: string[] = [];
    let pending = "";
    let consumed = 0;
    const cuts = boundaries(this.speak);
    for (const cut of cuts) {
      const piece = this.speak.slice(consumed, cut).replace(/\s+/g, " ").trim();
      consumed = cut;
      if (!piece) continue;
      pending = pending ? `${pending} ${piece}` : piece;
      if (pending.length >= MIN_SENTENCE_CHARS) {
        out.push(...splitLong(pending));
        pending = "";
      }
    }
    let rest = this.speak.slice(consumed);
    if (pending) rest = `${pending} ${rest}`;
    if (final) {
      const last = rest.replace(/\s+/g, " ").trim();
      if (last) {
        // A short tail joins the previous sentence when it fits.
        const previous = out.at(-1);
        if (last.length < MIN_SENTENCE_CHARS && previous && previous.length + last.length < MAX_SENTENCE_CHARS) {
          out[out.length - 1] = `${previous} ${last}`;
        } else {
          out.push(...splitLong(last));
        }
      }
      this.speak = "";
    } else {
      // Keep a separator so the held-back text never fuses with what follows.
      this.speak = rest;
    }
    return out;
  }
}
