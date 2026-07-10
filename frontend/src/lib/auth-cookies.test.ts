import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

describe("setAuthCookies", () => {
  afterEach(() => {
    vi.resetModules();
    vi.unstubAllEnvs();
  });

  beforeEach(() => {
    vi.resetModules();
  });

  it("does not mark cookies Secure in development", async () => {
    vi.stubEnv("NODE_ENV", "development");
    const cookieStore = createCookieStore();
    cookiesMock.mockResolvedValue(cookieStore);

    const { setAuthCookies } = await import("@/lib/auth-cookies");
    await setAuthCookies({ access: "a", refresh: "r" });

    expect(cookieStore.store.get("jarvis_access")?.secure).toBe(false);
  });

  it("marks cookies Secure in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    const cookieStore = createCookieStore();
    cookiesMock.mockResolvedValue(cookieStore);

    const { setAuthCookies } = await import("@/lib/auth-cookies");
    await setAuthCookies({ access: "a", refresh: "r" });

    expect(cookieStore.store.get("jarvis_access")?.secure).toBe(true);
    expect(cookieStore.store.get("jarvis_refresh")?.secure).toBe(true);
    expect(cookieStore.store.get("jarvis_session")?.secure).toBe(true);
  });
});
