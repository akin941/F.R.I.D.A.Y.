import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

interface StoredCookie {
  value: string;
  httpOnly?: boolean;
  sameSite?: string;
  secure?: boolean;
  path?: string;
  maxAge?: number;
}

function createCookieStore() {
  const store = new Map<string, StoredCookie>();
  return {
    store,
    get: (name: string) => (store.has(name) ? { name, value: store.get(name)!.value } : undefined),
    set: (name: string, value: string, options: Record<string, unknown> = {}) => {
      store.set(name, { value, ...options });
    },
    delete: () => undefined,
    has: (name: string) => store.has(name),
  };
}

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

function makeRequest(body: unknown, headers: Record<string, string> = {}) {
  return new NextRequest("http://localhost:3001/api/auth/enroll", {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      "user-agent": "Mozilla/5.0 (Macintosh) Chrome/140",
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

const SESSION = {
  user: { id: "u1", email: "owner@test.dev", name: "Owner", is_agent: false, is_owner: true },
  workspaces: [{ id: "w1", name: "Owner", type: "personal", role: "owner" }],
};

function backend(enrollStatus = 201, enrollBody: unknown = null) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/auth/enroll")) {
      return new Response(
        JSON.stringify(
          enrollBody ?? {
            device_secret: "jdv_secret-value",
            access: "access-token-value",
            refresh: "refresh-token-value",
            device: { id: "d1" },
          },
        ),
        { status: enrollStatus },
      );
    }
    if (url.endsWith("/auth/me")) {
      expect((init?.headers as Record<string, string>).Authorization).toBe(
        "Bearer access-token-value",
      );
      return new Response(JSON.stringify(SESSION), { status: 200 });
    }
    throw new Error(`Unexpected fetch to ${url}`);
  });
}

describe("POST /api/auth/enroll", () => {
  let cookieStore: ReturnType<typeof createCookieStore>;

  beforeEach(() => {
    vi.resetModules();
    cookieStore = createCookieStore();
    cookiesMock.mockResolvedValue(cookieStore);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("stores the device secret and session in HttpOnly cookies, never in the body", async () => {
    vi.stubGlobal("fetch", backend());
    const { POST } = await import("@/app/api/auth/enroll/route");
    const response = await POST(makeRequest({ code: "12345678", device_name: "Mac" }));

    expect(response.status).toBe(201);
    const serialized = JSON.stringify(await response.json());
    expect(serialized).not.toContain("jdv_secret-value");
    expect(serialized).not.toContain("access-token-value");
    expect(serialized).not.toContain("refresh-token-value");

    const device = cookieStore.store.get("friday_device");
    expect(device?.value).toBe("jdv_secret-value");
    expect(device?.httpOnly).toBe(true);
    expect(device?.sameSite).toBe("strict");
    expect(device?.path).toBe("/api/auth");
    expect(device?.maxAge).toBe(400 * 24 * 60 * 60);
    expect(device?.secure).toBe(false);

    expect(cookieStore.store.get("friday_access")?.path).toBe("/api");
    expect(cookieStore.store.get("friday_refresh")?.path).toBe("/api/auth");
    expect(cookieStore.store.get("friday_session")?.path).toBe("/");
  });

  it("marks the device cookie Secure in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubGlobal("fetch", backend());
    const { POST } = await import("@/app/api/auth/enroll/route");
    await POST(makeRequest({ code: "12345678", device_name: "Mac" }));
    expect(cookieStore.store.get("friday_device")?.secure).toBe(true);
  });

  it("passes the code, device name and browser user agent to the backend", async () => {
    const fetchMock = backend();
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/auth/enroll/route");
    await POST(makeRequest({ code: "ABCD-EFGH", device_name: "Mac" }));
    const [, init] = fetchMock.mock.calls[0]!;
    expect(JSON.parse(init!.body as string)).toEqual({
      code: "ABCD-EFGH",
      device_name: "Mac",
      user_agent: "Mozilla/5.0 (Macintosh) Chrome/140",
    });
    // No trusted proxy header configured: no client IP is forwarded.
    expect(init!.headers).not.toHaveProperty("X-Friday-Client-IP");
  });

  it("forwards the trusted proxy's client IP with the BFF key", async () => {
    vi.stubEnv("TRUSTED_CLIENT_IP_HEADER", "X-Real-IP");
    vi.stubEnv("FRIDAY_BFF_SHARED_SECRET", "bff-key");
    const fetchMock = backend();
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/auth/enroll/route");
    await POST(
      makeRequest({ code: "12345678", device_name: "Mac" }, { "x-real-ip": "192.168.1.5" }),
    );
    const [, init] = fetchMock.mock.calls[0]!;
    expect(init!.headers).toMatchObject({
      "X-Friday-Client-IP": "192.168.1.5",
      "X-Friday-BFF-Key": "bff-key",
    });
  });

  it("relays a refused code without setting any cookie", async () => {
    vi.stubGlobal(
      "fetch",
      backend(400, { detail: "Invalid or expired code.", code: "invalid_code" }),
    );
    const { POST } = await import("@/app/api/auth/enroll/route");
    const response = await POST(makeRequest({ code: "00000000", device_name: "Mac" }));
    expect(response.status).toBe(400);
    expect((await response.json()).code).toBe("invalid_code");
    expect(cookieStore.store.size).toBe(0);
  });

  it("rejects a cross-origin request before touching the backend", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/auth/enroll/route");
    const response = await POST(
      makeRequest({ code: "1", device_name: "x" }, { origin: "https://evil.example" }),
    );
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
