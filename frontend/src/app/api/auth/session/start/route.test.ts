import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

interface StoredCookie {
  value: string;
  [key: string]: unknown;
}

function createCookieStore(initial: Record<string, string> = {}) {
  const store = new Map<string, StoredCookie>(
    Object.entries(initial).map(([name, value]) => [name, { value }]),
  );
  const deleted: string[] = [];
  return {
    store,
    deleted,
    get: (name: string) => (store.has(name) ? { name, value: store.get(name)!.value } : undefined),
    set: (name: string, value: string, options: Record<string, unknown> = {}) => {
      store.set(name, { value, ...options });
    },
    delete: (arg: string | { name: string }) => {
      const name = typeof arg === "string" ? arg : arg.name;
      store.delete(name);
      deleted.push(name);
    },
    has: (name: string) => store.has(name),
  };
}

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

function makeRequest() {
  return new NextRequest("http://localhost:3001/api/auth/session/start", {
    method: "POST",
    headers: { origin: "http://localhost:3001", "content-type": "application/json" },
  });
}

describe("POST /api/auth/session/start", () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("mints a session from the device cookie", async () => {
    const cookieStore = createCookieStore({ friday_device: "jdv_secret" });
    cookiesMock.mockResolvedValue(cookieStore);
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ access: "a2", refresh: "r2", device: {} }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/auth/session/start/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/auth\/devices\/session$/);
    expect(JSON.parse(init.body as string)).toEqual({ device_secret: "jdv_secret" });
    expect(cookieStore.store.get("friday_access")?.value).toBe("a2");
    expect(cookieStore.store.get("friday_refresh")?.value).toBe("r2");
    expect(JSON.stringify(await response.json())).not.toContain("a2");
  });

  it("is 401 without a device cookie and never calls the backend", async () => {
    cookiesMock.mockResolvedValue(createCookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/auth/session/start/route");
    const response = await POST(makeRequest());
    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("drops the device cookie when the backend rejects the device", async () => {
    const cookieStore = createCookieStore({ friday_device: "jdv_revoked" });
    cookiesMock.mockResolvedValue(cookieStore);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ code: "device_rejected" }), { status: 401 })),
    );
    const { POST } = await import("@/app/api/auth/session/start/route");
    const response = await POST(makeRequest());
    expect(response.status).toBe(401);
    expect(cookieStore.deleted).toContain("friday_device");
  });

  it("keeps the device cookie when the backend is merely unavailable", async () => {
    const cookieStore = createCookieStore({ friday_device: "jdv_secret" });
    cookiesMock.mockResolvedValue(cookieStore);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
    const { POST } = await import("@/app/api/auth/session/start/route");
    const response = await POST(makeRequest());
    expect(response.status).toBe(503);
    expect(cookieStore.deleted).not.toContain("friday_device");
  });
});
