import { afterEach, describe, expect, it } from "vitest";
import { readDismissed } from "@/components/shell/SecurityNoticeBanner";

describe("security notice dismissals", () => {
  afterEach(() => window.localStorage.clear());

  it("carries pre-rename dismissals over once", () => {
    window.localStorage.setItem("jarvis:dismissed-security-notices", JSON.stringify(["dev1"]));
    expect(readDismissed()).toEqual(["dev1"]);
    expect(window.localStorage.getItem("jarvis:dismissed-security-notices")).toBeNull();
    expect(window.localStorage.getItem("friday:dismissed-security-notices")).toBe('["dev1"]');
  });

  it("prefers the new key when both exist", () => {
    window.localStorage.setItem("friday:dismissed-security-notices", JSON.stringify(["new"]));
    window.localStorage.setItem("jarvis:dismissed-security-notices", JSON.stringify(["old"]));
    expect(readDismissed()).toEqual(["new"]);
  });
});
