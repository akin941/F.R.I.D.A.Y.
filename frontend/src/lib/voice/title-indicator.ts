"use client";

/**
 * The "● Dinliyor" tab-title prefix while any microphone is open (D6):
 * push-to-talk on /voice and wake mode in the shell both hold it, so it is
 * reference-counted (never doubled) and re-applied when a navigation
 * replaces the document title.
 */
export const LISTENING_TITLE_PREFIX = "● Dinliyor — ";

let holders = 0;
let observer: MutationObserver | null = null;

// `document.title` collapses whitespace, so compare without the trailing space.
const MARK = LISTENING_TITLE_PREFIX.trim();

function apply() {
  if (holders > 0 && !document.title.startsWith(MARK)) {
    document.title = `${LISTENING_TITLE_PREFIX}${document.title}`;
  }
}

function strip() {
  if (document.title.startsWith(MARK)) {
    document.title = document.title.slice(MARK.length).trimStart();
  }
}

/** Hold the prefix; call the returned function to release it. */
export function holdListeningTitle(): () => void {
  if (typeof document === "undefined") return () => undefined;
  holders += 1;
  apply();
  if (!observer && typeof MutationObserver !== "undefined") {
    observer = new MutationObserver(() => apply());
    observer.observe(document.head, { childList: true, subtree: true, characterData: true });
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    holders = Math.max(0, holders - 1);
    if (holders === 0) {
      observer?.disconnect();
      observer = null;
      strip();
    }
  };
}
