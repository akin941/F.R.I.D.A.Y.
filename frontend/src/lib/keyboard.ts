import type { KeyboardEvent } from "react";

/**
 * Props that make a non-button element (e.g. a clickable `<Card>`) behave
 * like a button for keyboard and assistive-technology users: focusable,
 * announced as a button, and activated by Enter or Space -- the same keys a
 * native `<button>` responds to.
 */
export function clickableProps(onActivate: () => void) {
  return {
    role: "button" as const,
    tabIndex: 0,
    onClick: onActivate,
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
      // Only the element itself -- never a keypress bubbling up from a real
      // control nested inside it.
      if (event.target !== event.currentTarget) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onActivate();
      }
    },
  };
}
