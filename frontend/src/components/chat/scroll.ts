"use client";

import { useCallback, useLayoutEffect, useRef, useState, type RefObject } from "react";

/** How close to the bottom still counts as "following" the conversation. */
export const NEAR_BOTTOM_PX = 80;

export function isNearBottom(
  el: Pick<HTMLElement, "scrollTop" | "scrollHeight" | "clientHeight">,
  threshold = NEAR_BOTTOM_PX,
): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
}

/**
 * Smart scroll for the message list (Phase 4 branch 4).
 *
 * - While the reader is within `NEAR_BOTTOM_PX` of the bottom, new content
 *   (a streamed chunk, a reconciled reply, a draft card) keeps the list
 *   pinned to the bottom.
 * - Once they scroll up to read, new content never yanks them back down:
 *   `showPill` turns on instead ("Yeni mesaj ↓"), and `scrollToBottom`
 *   (the pill's action) jumps down and resumes following.
 * - `followSignal` changing (the reader sent a message) always resumes
 *   following: sending is an explicit "take me to the end".
 *
 * `contentSignature` must change only when content changes -- not on every
 * render -- or unrelated re-renders (typing in the composer) would raise
 * the pill.
 */
export function useStickToBottom(
  ref: RefObject<HTMLElement | null>,
  contentSignature: string,
  followSignal: number,
) {
  const followingRef = useRef(true);
  const [showPill, setShowPill] = useState(false);

  const scrollToBottom = useCallback(() => {
    const el = ref.current;
    followingRef.current = true;
    setShowPill(false);
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [ref]);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const near = isNearBottom(el);
    followingRef.current = near;
    if (near) setShowPill(false);
  }, [ref]);

  // A layout effect: the scroll position is corrected before the browser
  // paints the grown content, so a followed stream never visibly jumps. A
  // new `followSignal` (the reader sent a message) resumes following first.
  const seenFollowSignalRef = useRef(followSignal);
  useLayoutEffect(() => {
    if (followSignal !== seenFollowSignalRef.current) {
      seenFollowSignalRef.current = followSignal;
      followingRef.current = true;
    }
    const el = ref.current;
    if (followingRef.current) {
      if (el) el.scrollTop = el.scrollHeight;
      setShowPill(false);
    } else {
      setShowPill(true);
    }
  }, [contentSignature, followSignal, ref]);

  return { showPill, scrollToBottom, onScroll };
}
