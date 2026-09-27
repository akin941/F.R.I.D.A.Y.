import { describe, expect, it } from "vitest";
import { formatRemaining } from "@/lib/format/duration";

describe("formatRemaining", () => {
  it.each([
    [0, "0 sn"],
    [42, "42 sn"],
    [60, "1 dk"],
    [3599, "59 dk"],
    [3600, "1 sa"],
    [23 * 3600 + 12 * 60 + 5, "23 sa 12 dk"],
    [-5, "0 sn"],
  ])("%s seconds -> %s", (seconds, expected) => {
    expect(formatRemaining(seconds)).toBe(expected);
  });
});
