import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

function request(headers: Record<string, string>) {
  return new NextRequest("http://localhost:3001/api/auth/enroll", { headers });
}

async function load(env: Record<string, string>) {
  vi.resetModules();
  for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);
  return import("@/lib/client-ip");
}

describe("clientIpHeaders", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("forwards nothing without a configured trusted header (bare next start)", async () => {
    const { clientIpHeaders } = await load({ JARVIS_BFF_SHARED_SECRET: "k" });
    // x-forwarded-for / x-real-ip are client-controlled here: never read.
    const headers = clientIpHeaders(
      request({ "x-forwarded-for": "192.168.1.5", "x-real-ip": "192.168.1.5" }),
    );
    expect(headers).toEqual({});
  });

  it("forwards the configured header's IP with the BFF key", async () => {
    const { clientIpHeaders } = await load({
      TRUSTED_CLIENT_IP_HEADER: "X-Real-IP",
      JARVIS_BFF_SHARED_SECRET: "shared",
    });
    expect(clientIpHeaders(request({ "x-real-ip": "192.168.1.5" }))).toEqual({
      "X-Jarvis-Client-IP": "192.168.1.5",
      "X-Jarvis-BFF-Key": "shared",
    });
  });

  it("takes the right-most entry of a list-valued header", async () => {
    const { trustedClientIp } = await load({
      TRUSTED_CLIENT_IP_HEADER: "X-Forwarded-For",
      JARVIS_BFF_SHARED_SECRET: "shared",
    });
    expect(trustedClientIp(request({ "x-forwarded-for": "10.9.9.9, 203.0.113.7" }))).toBe(
      "203.0.113.7",
    );
  });

  it("ignores an unparseable value and a missing shared secret", async () => {
    let mod = await load({ TRUSTED_CLIENT_IP_HEADER: "X-Real-IP", JARVIS_BFF_SHARED_SECRET: "k" });
    expect(mod.clientIpHeaders(request({ "x-real-ip": "not-an-ip" }))).toEqual({});
    vi.unstubAllEnvs();
    mod = await load({ TRUSTED_CLIENT_IP_HEADER: "X-Real-IP" });
    expect(mod.clientIpHeaders(request({ "x-real-ip": "192.168.1.5" }))).toEqual({});
  });
});
