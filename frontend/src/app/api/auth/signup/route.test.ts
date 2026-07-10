import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

interface StoredCookie {
  value: string;
  [key: string]: unknown;
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
  return new NextRequest("http://localhost:3001/api/auth/signup", {
    method: "POST",
    headers: { origin: "http://localhost:3001", "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

describe("POST /api/auth/signup", () => {
  let cookieStore: ReturnType<typeof createCookieStore>;

  beforeEach(() => {
    cookieStore = createCookieStore();
    cookiesMock.mockResolvedValue(cookieStore);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/auth/signup")) {
          return new Response(
            JSON.stringify({
              user: { id: "u1", email: "bob@test.dev", name: "Bob", is_agent: false },
              tokens: { access: "access-token-value", refresh: "refresh-token-value" },
            }),
            { status: 201 },
          );
        }
        if (url.endsWith("/auth/me")) {
          return new Response(
            JSON.stringify({
              user: { id: "u1", email: "bob@test.dev", name: "Bob", is_agent: false },
              workspaces: [{ id: "w1", name: "Bob", type: "personal", role: "owner" }],
            }),
            { status: 200 },
          );
        }
        throw new Error(`Unexpected fetch to ${url}`);
      }),
    );
  });

  it("never includes the JWT in the response body", async () => {
    const { POST } = await import("@/app/api/auth/signup/route");
    const response = await POST(makeRequest({ email: "bob@test.dev", password: "pw" }));
    const body = await response.json();
    const serialized = JSON.stringify(body);
    expect(serialized).not.toContain("access-token-value");
    expect(serialized).not.toContain("refresh-token-value");
    expect(body.user.email).toBe("bob@test.dev");
  });

  it("sets HttpOnly cookies scoped to their paths", async () => {
    const { POST } = await import("@/app/api/auth/signup/route");
    await POST(makeRequest({ email: "bob@test.dev", password: "pw" }));

    const access = cookieStore.store.get("jarvis_access");
    const refresh = cookieStore.store.get("jarvis_refresh");
    expect(access?.value).toBe("access-token-value");
    expect(access?.httpOnly).toBe(true);
    expect(access?.path).toBe("/api");
    expect(refresh?.path).toBe("/api/auth/refresh");
  });

  it("rejects a mismatched Origin before touching the backend", async () => {
    const { POST } = await import("@/app/api/auth/signup/route");
    const response = await POST(
      makeRequest({ email: "bob@test.dev", password: "pw" }, { origin: "https://evil.example" }),
    );
    expect(response.status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before touching the backend", async () => {
    const { POST } = await import("@/app/api/auth/signup/route");
    const response = await POST(
      makeRequest({ email: "bob@test.dev", password: "pw" }, { "content-type": "text/plain" }),
    );
    expect(response.status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
});
