export const ACCESS_COOKIE = "friday_access";
export const REFRESH_COOKIE = "friday_refresh";
export const SESSION_COOKIE = "friday_session";
// The trusted device's secret (Phase 4 branch 2). HttpOnly, SameSite=Strict,
// path=/api/auth: only the auth route handlers ever see it.
export const DEVICE_COOKIE = "friday_device";

// Pre-rename names (Jarvis -> Friday, 2026-11-20). Still READ when the new
// cookie is absent -- so a browser paired before the rename stays paired --
// and cleared whenever the new name is set or cleared. Never set.
export const LEGACY_ACCESS_COOKIE = "jarvis_access";
export const LEGACY_REFRESH_COOKIE = "jarvis_refresh";
export const LEGACY_SESSION_COOKIE = "jarvis_session";
export const LEGACY_DEVICE_COOKIE = "jarvis_device";

interface CookieReader {
  has(name: string): boolean;
}

/** "The user probably has a session" -- under the new or the legacy name. */
export function hasSessionCookie(store: CookieReader): boolean {
  return store.has(SESSION_COOKIE) || store.has(LEGACY_SESSION_COOKIE);
}
