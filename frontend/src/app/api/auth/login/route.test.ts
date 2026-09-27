import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

interface StoredCookie {
  value: string;
  httpOnly?: boolean;
  sameSite?: string;
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

function makeRequest(body: unknown, origin = "http://localhost:3001") {
  return new NextRequest("http://localhost:3001/api/auth/login", {
    method: "POST",
    headers: { origin, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("POST /api/auth/login", () => {
  let cookieStore: ReturnType<typeof createCookieStore>;

  beforeEach(() => {
    cookieStore = createCookieStore();
    cookiesMock.mockResolvedValue(cookieStore);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/auth/login")) {
          return new Response(
            JSON.stringify({ access: "access-token-value", refresh: "refresh-token-value" }),
            { status: 200 },
          );
        }
        if (url.endsWith("/auth/me")) {
          return new Response(
            JSON.stringify({
              user: { id: "u1", email: "alice@test.dev", name: "Alice", is_agent: false },
              workspaces: [{ id: "w1", name: "Alice", type: "personal", role: "owner" }],
            }),
            { status: 200 },
          );
        }
        throw new Error(`Unexpected fetch to ${url}`);
      }),
    );
  });

  it("never includes the JWT in the response body", async () => {
    const { POST } = await import("@/app/api/auth/login/route");
    const response = await POST(makeRequest({ email: "alice@test.dev", password: "pw" }));
    const body = await response.json();
    const serialized = JSON.stringify(body);
    expect(serialized).not.toContain("access-token-value");
    expect(serialized).not.toContain("refresh-token-value");
    expect(body.user.email).toBe("alice@test.dev");
  });

  it("sets HttpOnly, SameSite=Lax cookies scoped to their paths", async () => {
    const { POST } = await import("@/app/api/auth/login/route");
    await POST(makeRequest({ email: "alice@test.dev", password: "pw" }));

    const access = cookieStore.store.get("jarvis_access");
    const refresh = cookieStore.store.get("jarvis_refresh");
    const session = cookieStore.store.get("jarvis_session");

    expect(access?.value).toBe("access-token-value");
    expect(access?.httpOnly).toBe(true);
    expect(access?.sameSite).toBe("lax");
    expect(access?.path).toBe("/api");

    expect(refresh?.value).toBe("refresh-token-value");
    expect(refresh?.httpOnly).toBe(true);
    expect(refresh?.path).toBe("/api/auth");

    expect(session?.httpOnly).toBe(true);
    expect(session?.path).toBe("/");
  });

  it("rejects a request with a mismatched Origin before touching the backend", async () => {
    const { POST } = await import("@/app/api/auth/login/route");
    const response = await POST(
      makeRequest({ email: "alice@test.dev", password: "pw" }, "https://evil.example"),
    );
    expect(response.status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
});
