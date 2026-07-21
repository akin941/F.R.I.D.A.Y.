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

  it("clears all auth cookies", async () => {
    const cookieStore = createCookieStore({
      jarvis_access: "a",
      jarvis_refresh: "r",
      jarvis_session: "1",
    });
    cookiesMock.mockResolvedValue(cookieStore);

    const { POST } = await import("@/app/api/auth/logout/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(200);
    expect(cookieStore.deleted).toEqual(
      expect.arrayContaining(["jarvis_access", "jarvis_refresh", "jarvis_session"]),
    );
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
