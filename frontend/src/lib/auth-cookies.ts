import "server-only";
import { cookies } from "next/headers";
import { ACCESS_COOKIE, REFRESH_COOKIE, SESSION_COOKIE } from "@/lib/cookie-names";

const isProduction = process.env.NODE_ENV === "production";

export { ACCESS_COOKIE, REFRESH_COOKIE, SESSION_COOKIE };

const THIRTY_MINUTES = 30 * 60;
const SEVEN_DAYS = 7 * 24 * 60 * 60;

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
      path: "/api/auth/refresh",
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
  store.delete({ name: REFRESH_COOKIE, path: "/api/auth/refresh" });
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
