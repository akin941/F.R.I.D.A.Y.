import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

function createCookieStore(initial: Record<string, string>) {
  const store = new Map(Object.entries(initial).map(([name, value]) => [name, { value }]));
  const deleted: string[] = [];
  return {
    store,
    deleted,
    get: (name: string) => (store.has(name) ? { name, value: store.get(name)!.value } : undefined),
    set: () => undefined,
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
  return new NextRequest("http://localhost:3001/api/auth/forget", {
    method: "POST",
    headers: { origin: "http://localhost:3001", "content-type": "application/json" },
  });
}

describe('POST /api/auth/forget ("Bu cihazı unut")', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("revokes the current device, ends the session and drops every cookie", async () => {
    const cookieStore = createCookieStore({
      jarvis_access: "a",
      jarvis_refresh: "r",
      jarvis_device: "jdv_x",
      jarvis_session: "1",
    });
    cookiesMock.mockResolvedValue(cookieStore);
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/auth/forget/route");
    const response = await POST(makeRequest());

    expect(response.status).toBe(200);
    const urls = fetchMock.mock.calls.map((call) => (call as unknown as [string])[0]);
    expect(urls[0]).toMatch(/\/auth\/devices\/current\/revoke$/);
    expect(urls[1]).toMatch(/\/auth\/logout$/);
    expect(cookieStore.deleted).toEqual(
      expect.arrayContaining(["jarvis_access", "jarvis_refresh", "jarvis_session", "jarvis_device"]),
    );
  });

  it("keeps everything when the revoke fails", async () => {
    const cookieStore = createCookieStore({ jarvis_access: "a", jarvis_device: "jdv_x" });
    cookiesMock.mockResolvedValue(cookieStore);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 500 })));
    const { POST } = await import("@/app/api/auth/forget/route");
    const response = await POST(makeRequest());
    expect(response.status).toBe(500);
    expect(cookieStore.deleted).toEqual([]);
  });
});
