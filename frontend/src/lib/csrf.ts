import "server-only";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { env } from "@/lib/env";

/**
 * Same-site cookies are not a sufficient CSRF defense on their own (older
 * browsers, misconfigured proxies); every mutation route handler verifies the
 * Origin header explicitly against APP_ORIGIN before touching the backend.
 */
export function rejectCrossOrigin(request: NextRequest): NextResponse | null {
  const origin = request.headers.get("origin");
  if (origin !== env.APP_ORIGIN) {
    return NextResponse.json({ detail: "Invalid origin." }, { status: 403 });
  }
  return null;
}

export function rejectNonJson(request: NextRequest): NextResponse | null {
  // Compare the media type only (strip a `; charset=...` parameter) so this
  // can't be satisfied by a substring like "application/json-lines".
  const mediaType = (request.headers.get("content-type") ?? "").split(";")[0]?.trim();
  if (mediaType !== "application/json") {
    return NextResponse.json(
      { detail: "Content-Type must be application/json." },
      { status: 403 },
    );
  }
  return null;
}

/**
 * Same origin-defense as `rejectNonJson`, for the one route that proxies a
 * `multipart/form-data` upload (knowledge file-upload) instead of JSON — a
 * multipart body always carries a per-request `boundary=...` parameter, so
 * only the base media type is compared here too.
 */
export function rejectNonMultipart(request: NextRequest): NextResponse | null {
  const mediaType = (request.headers.get("content-type") ?? "").split(";")[0]?.trim();
  if (mediaType !== "multipart/form-data") {
    return NextResponse.json(
      { detail: "Content-Type must be multipart/form-data." },
      { status: 403 },
    );
  }
  return null;
}
