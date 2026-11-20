import { afterEach, describe, expect, it, vi } from "vitest";
import { migrateLegacyStorageKey } from "@/lib/legacy-storage";

describe("migrateLegacyStorageKey", () => {
  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("moves the legacy value to the new key and removes the old one", () => {
    window.localStorage.setItem("jarvis:theme", "dark");
    migrateLegacyStorageKey("friday:theme", "jarvis:theme");
    expect(window.localStorage.getItem("friday:theme")).toBe("dark");
    expect(window.localStorage.getItem("jarvis:theme")).toBeNull();
  });

  it("never overwrites an existing new key", () => {
    window.localStorage.setItem("friday:theme", "light");
    window.localStorage.setItem("jarvis:theme", "dark");
    migrateLegacyStorageKey("friday:theme", "jarvis:theme");
    expect(window.localStorage.getItem("friday:theme")).toBe("light");
    expect(window.localStorage.getItem("jarvis:theme")).toBe("dark");
  });

  it("does nothing without a legacy value", () => {
    migrateLegacyStorageKey("friday:theme", "jarvis:theme");
    expect(window.localStorage.getItem("friday:theme")).toBeNull();
  });

  it("swallows storage errors", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => migrateLegacyStorageKey("friday:theme", "jarvis:theme")).not.toThrow();
  });
});
