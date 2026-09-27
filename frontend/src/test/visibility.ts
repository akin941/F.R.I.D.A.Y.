/**
 * Simulate the tab becoming hidden/visible. TanStack Query's focus manager
 * reads `document.visibilityState` and listens for `visibilitychange` on
 * window (the real event bubbles from document to window).
 */
export function setDocumentHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => (hidden ? "hidden" : "visible"),
  });
  document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
}
