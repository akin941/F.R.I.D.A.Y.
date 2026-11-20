import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
    get: (name: string) =>
      store.has(name) ? { name, value: store.get(name)!.value } : undefined,
    set: (
      name: string,
      value: string,
      options: Record<string, unknown> = {},
    ) => {
      store.set(name, { value, ...options });
    },
    delete: ({ name }: { name: string }) => {
      deleted.push(name);
      store.delete(name);
    },
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

    expect(cookieStore.store.get("friday_access")?.secure).toBe(false);
  });

  it("marks cookies Secure in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    const cookieStore = createCookieStore();
    cookiesMock.mockResolvedValue(cookieStore);

    const { setAuthCookies } = await import("@/lib/auth-cookies");
    await setAuthCookies({ access: "a", refresh: "r" });

    expect(cookieStore.store.get("friday_access")?.secure).toBe(true);
    expect(cookieStore.store.get("friday_refresh")?.secure).toBe(true);
    expect(cookieStore.store.get("friday_session")?.secure).toBe(true);
  });

  it("scopes the device cookie to the auth routes, strict same-site, 400 days", async () => {
    vi.stubEnv("NODE_ENV", "production");
    const cookieStore = createCookieStore();
    cookiesMock.mockResolvedValue(cookieStore);

    const { setDeviceCookie } = await import("@/lib/auth-cookies");
    await setDeviceCookie("jdv_x");

    expect(cookieStore.store.get("friday_device")).toEqual({
      value: "jdv_x",
      httpOnly: true,
      sameSite: "strict",
      secure: true,
      path: "/api/auth",
      maxAge: 400 * 24 * 60 * 60,
    });
  });
});

// Jarvis -> Friday rename (2026-11-20): a browser paired before the rename
// carries the jarvis_* cookies; it must stay signed in and paired.
describe("legacy (pre-rename) cookie names", () => {
  afterEach(() => {
    vi.resetModules();
    vi.unstubAllEnvs();
  });

  it("reads the legacy cookies when the new ones are absent", async () => {
    cookiesMock.mockResolvedValue(
      createCookieStore({
        jarvis_access: "a0",
        jarvis_refresh: "r0",
        jarvis_device: "jdv_old",
      }),
    );
    const { getAccessToken, getRefreshToken, getDeviceSecret } =
      await import("@/lib/auth-cookies");
    expect(await getAccessToken()).toBe("a0");
    expect(await getRefreshToken()).toBe("r0");
    expect(await getDeviceSecret()).toBe("jdv_old");
  });

  it("prefers the new cookies over the legacy ones", async () => {
    cookiesMock.mockResolvedValue(
      createCookieStore({
        friday_access: "new",
        jarvis_access: "old",
        friday_device: "d1",
        jarvis_device: "d0",
      }),
    );
    const { getAccessToken, getDeviceSecret } =
      await import("@/lib/auth-cookies");
    expect(await getAccessToken()).toBe("new");
    expect(await getDeviceSecret()).toBe("d1");
  });

  it("sets only the new names and drops the legacy session cookies", async () => {
    const cookieStore = createCookieStore({
      jarvis_access: "a0",
      jarvis_refresh: "r0",
      jarvis_session: "1",
    });
    cookiesMock.mockResolvedValue(cookieStore);
    const { setAuthCookies } = await import("@/lib/auth-cookies");
    await setAuthCookies({ access: "a1", refresh: "r1" });

    expect([...cookieStore.store.keys()].sort()).toEqual([
      "friday_access",
      "friday_refresh",
      "friday_session",
    ]);
    expect(cookieStore.deleted).toEqual(
      expect.arrayContaining([
        "jarvis_access",
        "jarvis_refresh",
        "jarvis_session",
      ]),
    );
  });

  it("clears both the new and the legacy names", async () => {
    const cookieStore = createCookieStore({
      jarvis_session: "1",
      jarvis_device: "jdv_old",
    });
    cookiesMock.mockResolvedValue(cookieStore);
    const { clearAuthCookies, clearDeviceCookie } =
      await import("@/lib/auth-cookies");
    await clearAuthCookies();
    await clearDeviceCookie();

    expect(cookieStore.deleted).toEqual(
      expect.arrayContaining([
        "friday_access",
        "friday_refresh",
        "friday_session",
        "jarvis_session",
        "friday_device",
        "jarvis_device",
      ]),
    );
    expect(cookieStore.store.size).toBe(0);
  });

  it("moves a legacy device secret to the new cookie name", async () => {
    const cookieStore = createCookieStore({ jarvis_device: "jdv_old" });
    cookiesMock.mockResolvedValue(cookieStore);
    const { migrateLegacyDeviceCookie } = await import("@/lib/auth-cookies");
    await migrateLegacyDeviceCookie();

    expect(cookieStore.store.get("friday_device")?.value).toBe("jdv_old");
    expect(cookieStore.store.has("jarvis_device")).toBe(false);
  });
});
