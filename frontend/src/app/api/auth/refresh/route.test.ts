import { beforeEach, describe, expect, it, vi } from "vitest";
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

function makeRequest(headers: Record<string, string> = {}) {
  return new NextRequest("http://localhost:3001/api/auth/refresh", {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
  });
}

describe("POST /api/auth/refresh", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rotation updates both the access and refresh cookies", async () => {
    const cookieStore = createCookieStore({ jarvis_refresh: "old-refresh" });
    cookiesMock.mockResolvedValue(cookieStore);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ access: "new-access", refresh: "new-refresh" }),
          { status: 200 },
        ),
      ),
    );

    const { POST } = await import("@/app/api/auth/refresh/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(200);
    expect(cookieStore.store.get("jarvis_access")?.value).toBe("new-access");
    expect(cookieStore.store.get("jarvis_refresh")?.value).toBe("new-refresh");
  });

  it("clears all auth cookies when the backend rejects the refresh token", async () => {
    const cookieStore = createCookieStore({ jarvis_refresh: "stale-refresh" });
    cookiesMock.mockResolvedValue(cookieStore);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "invalid" }), { status: 401 })),
    );

    const { POST } = await import("@/app/api/auth/refresh/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(401);
    expect(cookieStore.deleted).toEqual(
      expect.arrayContaining(["jarvis_access", "jarvis_refresh", "jarvis_session"]),
    );
  });

  it("returns 401 without calling the backend when no refresh cookie is present", async () => {
    const cookieStore = createCookieStore();
    cookiesMock.mockResolvedValue(cookieStore);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/auth/refresh/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a mismatched Origin before touching the backend", async () => {
    const cookieStore = createCookieStore({ jarvis_refresh: "old-refresh" });
    cookiesMock.mockResolvedValue(cookieStore);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/auth/refresh/route");
    const response = await POST(makeRequest({ origin: "https://evil.example" }));

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before touching the backend", async () => {
    const cookieStore = createCookieStore({ jarvis_refresh: "old-refresh" });
    cookiesMock.mockResolvedValue(cookieStore);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/auth/refresh/route");
    const response = await POST(makeRequest({ "content-type": "text/plain" }));

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("silently re-mints from the device cookie when the refresh token is rejected", async () => {
    const cookieStore = createCookieStore({
      jarvis_refresh: "stale-refresh",
      jarvis_device: "jdv_secret",
    });
    cookiesMock.mockResolvedValue(cookieStore);
    const fetchMock = vi.fn(async (url: string) =>
      url.endsWith("/auth/refresh")
        ? new Response(JSON.stringify({ detail: "blacklisted" }), { status: 401 })
        : new Response(JSON.stringify({ access: "minted-access", refresh: "minted-refresh" }), {
            status: 200,
          }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/auth/refresh/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(200);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      expect.stringMatching(/\/auth\/refresh$/),
      expect.stringMatching(/\/auth\/devices\/session$/),
    ]);
    expect(cookieStore.store.get("jarvis_access")?.value).toBe("minted-access");
    expect(cookieStore.deleted).not.toContain("jarvis_device");
  });

  it("drops the device cookie only when the device itself is rejected", async () => {
    const cookieStore = createCookieStore({ jarvis_device: "jdv_revoked" });
    cookiesMock.mockResolvedValue(cookieStore);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ code: "device_rejected" }), { status: 401 })),
    );

    const { POST } = await import("@/app/api/auth/refresh/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(401);
    expect(cookieStore.deleted).toEqual(
      expect.arrayContaining(["jarvis_access", "jarvis_session", "jarvis_device"]),
    );
  });
});
