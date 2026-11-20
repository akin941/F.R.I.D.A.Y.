import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { hasSessionCookie } from "@/lib/cookie-names";

const ENROLL_PATH = "/enroll";

// Every route of the `(shell)` group (src/app/(shell)/*). proxy.test.ts
// fails if a shell route is added without being listed here.
export const SHELL_PATHS = [
  "/agents",
  "/approvals",
  "/chat",
  "/connectors",
  "/deploys",
  "/devices",
  "/harness",
  "/hud",
  "/integrations",
  "/knowledge",
  "/memory",
  "/sessions",
  "/tasks",
  "/today",
  "/trash",
  "/voice",
  "/workspaces",
];

function isShellPath(pathname: string) {
  return SHELL_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));
}

/**
 * Optimistic navigation guard only — `friday_session` (or its pre-rename
 * `jarvis_session`) is not a credential, it
 * just signals "the user probably has a session." Every Route Handler still
 * re-authorizes against the (HttpOnly) access-token cookie independently, so
 * a stale/missing session cookie here can only cause an extra redirect, never
 * an authorization bypass. Without a session every shell route goes to
 * /enroll, which first tries to resume from this browser's trusted-device
 * cookie (invisible here: it is scoped to /api/auth) before asking for a
 * pairing code (Phase 4 branch 2).
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = hasSessionCookie(request.cookies);

  if (isShellPath(pathname) && !hasSession) {
    return NextResponse.redirect(new URL(ENROLL_PATH, request.url));
  }

  if (pathname === ENROLL_PATH && hasSession) {
    return NextResponse.redirect(new URL("/chat", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/enroll",
    "/agents/:path*",
    "/approvals/:path*",
    "/chat/:path*",
    "/connectors/:path*",
    "/deploys/:path*",
    "/devices/:path*",
    "/harness/:path*",
    "/hud/:path*",
    "/integrations/:path*",
    "/knowledge/:path*",
    "/memory/:path*",
    "/sessions/:path*",
    "/tasks/:path*",
    "/today/:path*",
    "/trash/:path*",
    "/voice/:path*",
    "/workspaces/:path*",
  ],
};
