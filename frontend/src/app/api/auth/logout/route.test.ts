import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

function createCookieStore(initial: Record<string, string> = {}) {
  const store = new Map<string, { value: string }>(
    Object.entries(initial).map(([name, value]) => [name, { value }]),
  );
  const deleted: string[] = [];
  return {
    store,
    deleted,
    get: (name: string) => (store.has(name) ? { name, value: store.get(name)!.value } : undefined),
    set: (name: string, value: string) => {
      store.set(name, { value });
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
  return new NextRequest("http://localhost:3001/api/auth/logout", {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
  });
}

describe("POST /api/auth/logout", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("blacklists the refresh token at the backend and clears all auth cookies", async () => {
    const cookieStore = createCookieStore({
      friday_access: "a",
      friday_refresh: "r",
      friday_session: "1",
    });
    cookiesMock.mockResolvedValue(cookieStore);
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/auth/logout/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/auth\/logout$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ refresh: "r" });
    // The refresh token is the credential; a possibly-expired access token
    // must not be sent (it would make the backend reject the call).
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect(cookieStore.deleted).toEqual(
      expect.arrayContaining(["friday_access", "friday_refresh", "friday_session"]),
    );
  });

  it("still clears the cookies when the backend rejects the token", async () => {
    const cookieStore = createCookieStore({ friday_refresh: "already-blacklisted" });
    cookiesMock.mockResolvedValue(cookieStore);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));

    const { POST } = await import("@/app/api/auth/logout/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(200);
    expect(cookieStore.deleted).toEqual(expect.arrayContaining(["friday_refresh"]));
  });

  it("still clears the cookies when the backend is unreachable", async () => {
    const cookieStore = createCookieStore({ friday_refresh: "r" });
    cookiesMock.mockResolvedValue(cookieStore);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );

    const { POST } = await import("@/app/api/auth/logout/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(200);
    expect(cookieStore.deleted).toEqual(expect.arrayContaining(["friday_refresh"]));
  });

  it("skips the backend call when there is no refresh cookie", async () => {
    const cookieStore = createCookieStore({ friday_session: "1" });
    cookiesMock.mockResolvedValue(cookieStore);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/auth/logout/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(200);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a mismatched Origin", async () => {
    const cookieStore = createCookieStore();
    cookiesMock.mockResolvedValue(cookieStore);

    const { POST } = await import("@/app/api/auth/logout/route");
    const response = await POST(makeRequest({ origin: "https://evil.example" }));

    expect(response.status).toBe(403);
    expect(cookieStore.deleted).toEqual([]);
  });

  it("rejects a non-JSON Content-Type", async () => {
    const cookieStore = createCookieStore();
    cookiesMock.mockResolvedValue(cookieStore);

    const { POST } = await import("@/app/api/auth/logout/route");
    const response = await POST(makeRequest({ "content-type": "text/plain" }));

    expect(response.status).toBe(403);
    expect(cookieStore.deleted).toEqual([]);
  });
});
