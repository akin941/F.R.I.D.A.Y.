import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/cookie-names";

const PUBLIC_PATHS = ["/login", "/signup"];
const PROTECTED_PATHS = ["/chat", "/today"];

/**
 * Optimistic navigation guard only — `jarvis_session` is not a credential, it
 * just signals "the user probably has a session." Every Route Handler still
 * re-authorizes against the (HttpOnly) access-token cookie independently, so
 * a stale/missing session cookie here can only cause an extra redirect, never
 * an authorization bypass.
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = request.cookies.has(SESSION_COOKIE);

  if (PROTECTED_PATHS.some((path) => pathname.startsWith(path)) && !hasSession) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  if (PUBLIC_PATHS.some((path) => pathname.startsWith(path)) && hasSession) {
    return NextResponse.redirect(new URL("/chat", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/login", "/signup", "/chat/:path*", "/today/:path*"],
};
