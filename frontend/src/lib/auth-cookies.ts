import "server-only";
import { cookies } from "next/headers";
import { ACCESS_COOKIE, REFRESH_COOKIE, SESSION_COOKIE } from "@/lib/cookie-names";

const isProduction = process.env.NODE_ENV === "production";

export { ACCESS_COOKIE, REFRESH_COOKIE, SESSION_COOKIE };

const THIRTY_MINUTES = 30 * 60;
const SEVEN_DAYS = 7 * 24 * 60 * 60;

// The refresh cookie reaches the /api/auth/* route handlers only: the
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

export async function setAuthCookies({ access, refresh }: Tokens) {
  const store = await cookies();
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
}

export async function getAccessToken(): Promise<string | undefined> {
  const store = await cookies();
  return store.get(ACCESS_COOKIE)?.value;
}

export async function getRefreshToken(): Promise<string | undefined> {
  const store = await cookies();
  return store.get(REFRESH_COOKIE)?.value;
}
