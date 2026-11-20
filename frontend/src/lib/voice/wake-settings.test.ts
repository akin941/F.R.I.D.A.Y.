import { afterEach, describe, expect, it } from "vitest";
import {
  WAKE_IDLE_KEY,
  WAKE_THRESHOLD_KEY,
  loadWakeSettings,
  saveWakeSettings,
} from "@/lib/voice/wake-settings";
import { LISTENING_TITLE_PREFIX, holdListeningTitle } from "@/lib/voice/title-indicator";

afterEach(() => window.localStorage.clear());

describe("wake settings (per device)", () => {
  it("defaults, clamps and persists threshold and idle minutes", () => {
    expect(loadWakeSettings()).toEqual({ threshold: 0.5, idleMinutes: 60 });
    expect(saveWakeSettings({ threshold: 0.99, idleMinutes: 0 })).toEqual({ threshold: 0.95, idleMinutes: 1 });
    expect(window.localStorage.getItem(WAKE_THRESHOLD_KEY)).toBe("0.95");
    expect(window.localStorage.getItem(WAKE_IDLE_KEY)).toBe("1");
    expect(loadWakeSettings()).toEqual({ threshold: 0.95, idleMinutes: 1 });
  });

  it("never stores an armed flag (arming is not restored on reload)", () => {
    saveWakeSettings({ threshold: 0.6 });
    const keys = Object.keys(window.localStorage);
    expect(keys.some((k) => /arm/i.test(k))).toBe(false);
  });
});

describe("listening title prefix", () => {
  it("is reference-counted: two holders, one prefix, removed with the last", () => {
    document.title = "Friday";
    const a = holdListeningTitle();
    const b = holdListeningTitle();
    expect(document.title).toBe(`${LISTENING_TITLE_PREFIX}Friday`);
    a();
    expect(document.title.startsWith("● Dinliyor")).toBe(true);
    b();
    expect(document.title).toBe("Friday");
  });
});
