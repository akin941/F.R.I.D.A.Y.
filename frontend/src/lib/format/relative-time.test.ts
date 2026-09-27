import { describe, expect, it } from "vitest";
import { formatDateTime, formatRelativeTime } from "@/lib/format/relative-time";

const NOW = new Date("2026-09-27T12:00:00Z");

describe("formatRelativeTime (tr-TR)", () => {
  it.each([
    ["2026-09-27T11:59:50Z", "az önce"],
    ["2026-09-27T11:55:00Z", "5 dakika önce"],
    ["2026-09-27T10:00:00Z", "2 saat önce"],
    ["2026-09-26T12:00:00Z", "dün"],
    ["2026-09-30T12:00:00Z", "3 gün sonra"],
    ["2026-07-27T12:00:00Z", "2 ay önce"],
  ])("%s -> %s", (value, expected) => {
    expect(formatRelativeTime(value, NOW)).toBe(expected);
  });

  it("returns an empty string for an invalid date", () => {
    expect(formatRelativeTime("not a date", NOW)).toBe("");
    expect(formatDateTime("not a date")).toBe("");
  });

  it("formats absolute dates with Turkish month names", () => {
    expect(formatDateTime("2026-09-27T12:00:00Z")).toMatch(/Eyl 2026/);
  });
});
