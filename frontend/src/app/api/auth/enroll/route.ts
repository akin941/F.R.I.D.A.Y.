import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { clientIpHeaders } from "@/lib/client-ip";
import { setAuthCookies, setDeviceCookie } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";
import type { ApiErrorBody, SessionResponse } from "@/types/api";

export const runtime = "nodejs";

/**
 * Device enrollment (Phase 4 branch 2): exchanges a pairing code or the
 * remote access key for a trusted device. The device secret and the first
 * JWT pair go into HttpOnly cookies; the browser only ever gets the
 * session summary back.
 */
export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  let code: unknown;
  let deviceName: unknown;
  try {
    ({ code, device_name: deviceName } = await request.json());
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }
  if (typeof code !== "string" || typeof deviceName !== "string") {
    return NextResponse.json(
      { detail: "code and device_name are required." },
      { status: 400 },
    );
  }

  const enrollResponse = await backendFetch("/auth/enroll", {
    method: "POST",
    body: {
      code,
      device_name: deviceName,
      user_agent: (request.headers.get("user-agent") ?? "").slice(0, 1000),
    },
    headers: clientIpHeaders(request),
  });
  if (!enrollResponse.ok) {
    const body = (await enrollResponse.json().catch(() => ({}))) as ApiErrorBody;
    return NextResponse.json(body, { status: enrollResponse.status });
  }
  const enrolled = (await enrollResponse.json()) as {
    device_secret: string;
    access: string;
    refresh: string;
  };

  const meResponse = await backendFetch("/auth/me", { accessToken: enrolled.access });
  if (!meResponse.ok) {
    return NextResponse.json(
      { detail: "Enrollment succeeded but session lookup failed." },
      { status: 502 },
    );
  }
  const session = (await meResponse.json()) as SessionResponse;

  // Cookies are written last, only once every upstream call succeeded.
  await setDeviceCookie(enrolled.device_secret);
  await setAuthCookies({ access: enrolled.access, refresh: enrolled.refresh });
  return NextResponse.json(session, { status: 201 });
}
