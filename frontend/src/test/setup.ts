import { vi } from "vitest";
import "@testing-library/jest-dom/vitest";

process.env.BACKEND_API_URL ??= "http://127.0.0.1:8001/api/v1";
process.env.APP_ORIGIN ??= "http://localhost:3001";

// `server-only` throws when it detects a browser-like global scope, which is
// exactly what jsdom is — Next.js's own bundler exempts server bundles via a
// build-time condition Vitest doesn't set. The guard is a compile-time canary
// for accidental client imports, irrelevant to testing the module's logic.
vi.mock("server-only", () => ({}));

// jsdom implements neither API; Radix's ScrollArea/Select/Sheet reference
// them directly without feature-detecting, so components using them would
// otherwise throw on mount under jsdom.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;

if (typeof window !== "undefined" && !window.HTMLElement.prototype.hasPointerCapture) {
  window.HTMLElement.prototype.hasPointerCapture = () => false;
  window.HTMLElement.prototype.setPointerCapture = () => {};
  window.HTMLElement.prototype.releasePointerCapture = () => {};
}

// jsdom doesn't implement scrollIntoView at all; Radix Select calls it
// internally on the highlighted option whenever a dropdown actually opens
// (mounting a Select that's never interacted with never hits this path,
// which is why no prior test needed this stub).
if (typeof window !== "undefined" && !window.HTMLElement.prototype.scrollIntoView) {
  window.HTMLElement.prototype.scrollIntoView = () => {};
}

// jsdom has no matchMedia; next-themes (system theme) and sonner query it.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
