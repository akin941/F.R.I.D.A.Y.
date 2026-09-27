import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

export const runtime = "nodejs";

// The inbox's exact-match filters, forwarded as-is (validated by Django).
const FORWARDED_FILTERS = ["status", "tool", "origin", "requested_channel"] as const;

export async function GET(request: NextRequest) {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  const params = request.nextUrl.searchParams;
  // `scope=all` is the cross-workspace inbox (Phase 4 branch 5): the
  // workspace is then an optional narrowing filter; otherwise it is
  // required, as before.
  const scope = params.get("scope");
  const workspace = params.get("workspace");
  if (scope !== "all" && !workspace) {
    return NextResponse.json(
      { detail: "workspace query parameter is required." },
      { status: 400 },
    );
  }

  const query = new URLSearchParams();
  if (scope) query.set("scope", scope);
  if (workspace) query.set("workspace", workspace);
  for (const name of FORWARDED_FILTERS) {
    const value = params.get(name);
    if (value) query.set(name, value);
  }

  const response = await backendFetch(`/approvals/requests?${query.toString()}`, { accessToken });
  const body = await response.json().catch(() => []);
  return NextResponse.json(body, { status: response.status });
}

export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonJson(request);
  if (contentTypeError) return contentTypeError;

  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body." }, { status: 400 });
  }

  const response = await backendFetch("/approvals/requests", {
    method: "POST",
    accessToken,
    body: payload,
  });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
