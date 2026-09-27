export const ACCESS_COOKIE = "jarvis_access";
export const REFRESH_COOKIE = "jarvis_refresh";
export const SESSION_COOKIE = "jarvis_session";
// The trusted device's secret (Phase 4 branch 2). HttpOnly, SameSite=Strict,
// path=/api/auth: only the auth route handlers ever see it.
export const DEVICE_COOKIE = "jarvis_device";
