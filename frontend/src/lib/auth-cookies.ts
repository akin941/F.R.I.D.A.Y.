import "server-only";
import { cookies } from "next/headers";
import {
  ACCESS_COOKIE,
  DEVICE_COOKIE,
  LEGACY_ACCESS_COOKIE,
  LEGACY_DEVICE_COOKIE,
  LEGACY_REFRESH_COOKIE,
  LEGACY_SESSION_COOKIE,
  REFRESH_COOKIE,
  SESSION_COOKIE,
} from "@/lib/cookie-names";

const isProduction = process.env.NODE_ENV === "production";

export { ACCESS_COOKIE, DEVICE_COOKIE, REFRESH_COOKIE, SESSION_COOKIE };

const THIRTY_MINUTES = 30 * 60;
const SEVEN_DAYS = 7 * 24 * 60 * 60;
// Browsers cap cookie lifetimes at 400 days; the device's real lifetime is
// governed server-side (idle expiry, revocation).
export const DEVICE_COOKIE_MAX_AGE = 400 * 24 * 60 * 60;

// The refresh cookie (and the device cookie) reach the /api/auth/* route
// handlers only: the
// refresh route rotates it and the logout route sends it to the backend to
// be blacklisted. (Until Phase 4 it was scoped to /api/auth/refresh, so
// logout could not see it; a leftover cookie at that older, more specific
// path is shadowed by this one -- Next.js keeps the last same-name cookie
// of the Cookie header, which browsers order longest-path first -- and
// expires on its own.)
export const REFRESH_COOKIE_PATH = "/api/auth";

interface Tokens {
  access: string;
  refresh?: string;
}

type CookieStore = Awaited<ReturnType<typeof cookies>>;

// Pre-rename (Jarvis -> Friday) session cookies: dropped whenever the new
// names are set or cleared, so a migrated browser carries only one set.
function deleteLegacyAuthCookies(store: CookieStore) {
  if (store.has(LEGACY_ACCESS_COOKIE))
    store.delete({ name: LEGACY_ACCESS_COOKIE, path: "/api" });
  if (store.has(LEGACY_REFRESH_COOKIE)) {
    store.delete({ name: LEGACY_REFRESH_COOKIE, path: REFRESH_COOKIE_PATH });
  }
  if (store.has(LEGACY_SESSION_COOKIE))
    store.delete({ name: LEGACY_SESSION_COOKIE, path: "/" });
}

export async function setAuthCookies({ access, refresh }: Tokens) {
  const store = await cookies();
  deleteLegacyAuthCookies(store);
  store.set(ACCESS_COOKIE, access, {
    httpOnly: true,
    sameSite: "lax",
    secure: isProduction,
    path: "/api",
    maxAge: THIRTY_MINUTES,
  });
  if (refresh) {
    store.set(REFRESH_COOKIE, refresh, {
      httpOnly: true,
      sameSite: "lax",
      secure: isProduction,
      path: REFRESH_COOKIE_PATH,
      maxAge: SEVEN_DAYS,
    });
  }
  store.set(SESSION_COOKIE, "1", {
    httpOnly: true,
    sameSite: "lax",
    secure: isProduction,
    path: "/",
    maxAge: SEVEN_DAYS,
  });
}

export async function clearAuthCookies() {
  const store = await cookies();
  store.delete({ name: ACCESS_COOKIE, path: "/api" });
  store.delete({ name: REFRESH_COOKIE, path: REFRESH_COOKIE_PATH });
  store.delete({ name: SESSION_COOKIE, path: "/" });
  deleteLegacyAuthCookies(store);
}

export async function getAccessToken(): Promise<string | undefined> {
  const store = await cookies();
  return (
    store.get(ACCESS_COOKIE)?.value ?? store.get(LEGACY_ACCESS_COOKIE)?.value
  );
}

export async function getRefreshToken(): Promise<string | undefined> {
  const store = await cookies();
  return (
    store.get(REFRESH_COOKIE)?.value ?? store.get(LEGACY_REFRESH_COOKIE)?.value
  );
}

/**
 * The trusted device's secret (Phase 4 branch 2): minted once at /enroll and
 * used by the BFF to re-mint a session when a refresh fails. Strict
 * same-site and scoped to the auth route handlers; never readable by
 * JavaScript and never returned in a response body.
 */
export async function setDeviceCookie(secret: string) {
  const store = await cookies();
  if (store.has(LEGACY_DEVICE_COOKIE)) {
    store.delete({ name: LEGACY_DEVICE_COOKIE, path: REFRESH_COOKIE_PATH });
  }
  store.set(DEVICE_COOKIE, secret, {
    httpOnly: true,
    sameSite: "strict",
    secure: isProduction,
    path: REFRESH_COOKIE_PATH,
    maxAge: DEVICE_COOKIE_MAX_AGE,
  });
}

export async function getDeviceSecret(): Promise<string | undefined> {
  const store = await cookies();
  return (
    store.get(DEVICE_COOKIE)?.value ?? store.get(LEGACY_DEVICE_COOKIE)?.value
  );
}

/**
 * A browser paired before the rename holds its secret under the legacy
 * name only: re-set it under the new name (which drops the legacy one).
 */
export async function migrateLegacyDeviceCookie() {
  const store = await cookies();
  const legacy = store.get(LEGACY_DEVICE_COOKIE)?.value;
  if (legacy && !store.has(DEVICE_COOKIE)) await setDeviceCookie(legacy);
}

export async function clearDeviceCookie() {
  const store = await cookies();
  store.delete({ name: DEVICE_COOKIE, path: REFRESH_COOKIE_PATH });
  store.delete({ name: LEGACY_DEVICE_COOKIE, path: REFRESH_COOKIE_PATH });
}
