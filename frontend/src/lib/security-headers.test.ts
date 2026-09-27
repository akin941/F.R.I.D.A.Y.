import { describe, expect, it } from "vitest";
import { securityHeaderRules } from "./security-headers";

describe("securityHeaderRules", () => {
  it("sets the browser-facing security headers on every route in production", () => {
    const rules = securityHeaderRules(true);
    expect(rules).toHaveLength(1);
    expect(rules[0]!.source).toBe("/:path*");
    const headers = Object.fromEntries(rules[0]!.headers.map((h) => [h.key, h.value]));
    expect(headers["Strict-Transport-Security"]).toMatch(/max-age=\d+/);
    expect(headers["Content-Security-Policy"]).toBe("frame-ancestors 'none'");
    expect(headers["X-Frame-Options"]).toBe("DENY");
    expect(headers["X-Content-Type-Options"]).toBe("nosniff");
    expect(headers["Referrer-Policy"]).toBe("same-origin");
  });

  it("adds nothing outside production", () => {
    expect(securityHeaderRules(false)).toEqual([]);
  });
});
