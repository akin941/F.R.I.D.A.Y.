import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  const workspace = request.nextUrl.searchParams.get("workspace");
  if (!workspace) {
    return NextResponse.json(
      { detail: "workspace query parameter is required." },
      { status: 400 },
    );
  }

  const query = new URLSearchParams({ workspace });
  const status = request.nextUrl.searchParams.get("status");
  if (status) query.set("status", status);
  // `due_before`/`due_after` are the *client's* local calendar date
  // (YYYY-MM-DD) -- this route forwards them as-is; it never computes
  // "today" itself (brief §2 -- the Today page owns that computation).
  const dueBefore = request.nextUrl.searchParams.get("due_before");
  if (dueBefore) query.set("due_before", dueBefore);
  const dueAfter = request.nextUrl.searchParams.get("due_after");
  if (dueAfter) query.set("due_after", dueAfter);

  const response = await backendFetch(`/tasks?${query.toString()}`, { accessToken });
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

  const response = await backendFetch("/tasks", {
    method: "POST",
    accessToken,
    body: payload,
  });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
