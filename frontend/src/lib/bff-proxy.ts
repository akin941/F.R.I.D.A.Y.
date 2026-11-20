import "server-only";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";
import { isValidUuid } from "@/lib/uuid";

/**
 * Shared plumbing for the Phase 5 branch 9 BFF routes (connectors,
 * deploys, the harness PR panel) -- the same discipline every hand-written
 * route follows: path segments validated before interpolation, the
 * Origin/Content-Type checks before any cookie or backend access on a
 * mutation, the bearer token only server-side, and the backend's status
 * and body forwarded as-is (authorization stays in the backend).
 */

const NOT_FOUND = () => NextResponse.json({ detail: "Not found." }, { status: 404 });
const NOT_AUTHENTICATED = () =>
  NextResponse.json({ detail: "Not authenticated." }, { status: 401 });

/** A connector name as the backend names manifests (`[a-z][a-z0-9_]*`). */
const CONNECTOR_NAME = /^[a-z][a-z0-9_]{0,63}$/;

export function isConnectorName(value: string): boolean {
  return CONNECTOR_NAME.test(value);
}

/** Every segment must be a UUID (or pass `check`) before it reaches a backend path. */
export function validSegments(
  segments: string[],
  check: (value: string) => boolean = isValidUuid,
): boolean {
  return segments.every((segment) => check(segment));
}

interface QuerySpec {
  /** Params that must be present (400 otherwise). */
  required?: string[];
  /** Params forwarded when present. */
  optional?: string[];
  /** Params that, when present, must be UUIDs (400 otherwise). */
  uuids?: string[];
}

function forwardQuery(request: NextRequest, spec: QuerySpec): URLSearchParams | NextResponse {
  const source = request.nextUrl.searchParams;
  const query = new URLSearchParams();
  for (const name of spec.required ?? []) {
    const value = source.get(name);
    if (!value) {
      return NextResponse.json({ detail: `${name} query parameter is required.` }, { status: 400 });
    }
    query.set(name, value);
  }
  for (const name of spec.optional ?? []) {
    const value = source.get(name);
    if (value) query.set(name, value);
  }
  for (const name of spec.uuids ?? []) {
    const value = query.get(name);
    if (value && !isValidUuid(value)) {
      return NextResponse.json({ detail: `${name} must be a valid UUID.` }, { status: 400 });
    }
  }
  return query;
}

async function forward(response: Response, emptyBody: unknown) {
  if (response.status === 202 || response.status === 204) {
    return new NextResponse(null, { status: response.status });
  }
  const body = await response.json().catch(() => emptyBody);
  return NextResponse.json(body, { status: response.status });
}

/** GET `backendPath` (+ the allowed query params) with the session's token. */
export async function proxyGet(
  request: NextRequest,
  backendPath: string,
  spec: QuerySpec = {},
  emptyBody: unknown = {},
) {
  const accessToken = await getAccessToken();
  if (!accessToken) return NOT_AUTHENTICATED();
  const query = forwardQuery(request, spec);
  if (query instanceof NextResponse) return query;
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  const response = await backendFetch(`${backendPath}${suffix}`, { accessToken });
  return forward(response, emptyBody);
}

/**
 * A mutation (POST/PUT/PATCH) to `backendPath`. Origin and Content-Type
 * are checked first, then the session; `withBody` forwards the JSON body.
 */
export async function proxyMutation(
  request: NextRequest,
  method: "POST" | "PUT" | "PATCH",
  backendPath: string,
  { withBody = true }: { withBody?: boolean } = {},
) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  const accessToken = await getAccessToken();
  if (!accessToken) return NOT_AUTHENTICATED();

  let payload: unknown = undefined;
  if (withBody) {
    try {
      payload = await request.json();
    } catch {
      return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
    }
  }
  const response = await backendFetch(backendPath, { method, accessToken, body: payload });
  return forward(response, {});
}

export { NOT_FOUND as notFound };
