/**
 * Deterministic voice routes (Phase 6 A7), checked on the transcript
 * BEFORE anything goes to the brain (the chat turn).
 *
 * Instant client-side commands only. A route may stop audio, mute, read a
 * receipt or file a PROPOSAL -- never approve, reject, confirm or cancel
 * anything (D8, ADR-015): a spoken "evet, onayla" is routed to a fixed
 * reply that says decisions are made on screen, and nothing else happens.
 * Nothing here maps a transcript to a click.
 */

export type VoiceRoute =
  /** "dur" / "bekle" / "stop": stop playback, dispatch nothing. */
  | { kind: "interrupt" }
  /** "sessize al": the shell mute flag. */
  | { kind: "mute" }
  /** "hatırla ki …": a pending_approval memory proposal (A6). */
  | { kind: "remember"; content: string }
  /** "bu ne kadar tuttu?": the ModelUsage receipt (a read). */
  | { kind: "receipt" }
  /** A spoken yes/approve: never a confirmation (D8). */
  | { kind: "affirmation" }
  /** Everything else goes to the brain as a normal voice turn. */
  | { kind: "brain"; text: string };

/** Turkish-aware normalisation: lower case (İ->i, I->ı), no punctuation. */
export function normalizeUtterance(text: string): string {
  return text
    .toLocaleLowerCase("tr")
    .replace(/[’']/g, "'")
    .replace(/[^\p{L}\p{N}'\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export const INTERRUPT_WORDS = new Set([
  "dur",
  "durdur",
  "bekle",
  "sus",
  "stop",
  "yeter",
  "tamam dur",
  "dur dur",
  "friday dur",
]);

const MUTE_PHRASES = new Set(["sessize al", "sesi kapat", "mikrofonu kapat", "sessiz ol"]);

const AFFIRMATIONS = new Set([
  "evet",
  "evet onayla",
  "onayla",
  "onaylıyorum",
  "onay",
  "onay ver",
  "kabul",
  "kabul et",
  "tamam onayla",
  "evet yap",
  "yap",
  "devam et",
  "reddet",
  "iptal et",
  "yes",
  "approve",
  "confirm",
  "reject",
  "cancel",
]);

const RECEIPT_RE = /^(?:peki |friday )?(?:bu|bu tur|bu cevap|bu yanıt|son tur)?\s*(?:ne kadar tuttu|kaça mal oldu|kaç para tuttu|maliyeti ne(?: kadar)?(?: oldu)?)$/;

// Only the explicit forms: "hatırla ki …", "unutma ki …", "hatırla: …".
// ("hatırla bakalım dün ne dedik" is a question for the brain.)
const REMEMBER_RE = /^\s*(?:friday[,\s]+)?(?:hatırla ki|unutma ki|hatırla\s*:)[\s,:]*(\S[^]*)$/iu;

export function isInterruptWord(text: string): boolean {
  return INTERRUPT_WORDS.has(normalizeUtterance(text));
}

export function routeTranscript(text: string): VoiceRoute {
  const normalized = normalizeUtterance(text);
  if (!normalized) return { kind: "brain", text: "" };
  if (INTERRUPT_WORDS.has(normalized)) return { kind: "interrupt" };
  if (MUTE_PHRASES.has(normalized)) return { kind: "mute" };
  if (AFFIRMATIONS.has(normalized)) return { kind: "affirmation" };
  if (RECEIPT_RE.test(normalized)) return { kind: "receipt" };
  // The content keeps the owner's own wording (only the prefix is cut).
  const content = stripRememberPrefix(text);
  if (content) return { kind: "remember", content };
  return { kind: "brain", text: text.trim() };
}

/** "Hatırla ki annemin doğum günü 3 Mart." -> "Annemin doğum günü 3 Mart." */
export function stripRememberPrefix(text: string): string {
  const match = REMEMBER_RE.exec(text.toLocaleLowerCase("tr"));
  if (!match) return "";
  const start = text.length - (match[1] ?? "").length;
  const content = text.slice(start).trim();
  if (!content) return "";
  return content.charAt(0).toLocaleUpperCase("tr") + content.slice(1);
}
