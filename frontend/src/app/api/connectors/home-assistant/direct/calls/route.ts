import { NextResponse, type NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 13b D11 (ADR-018): the direct calls, newest first (owner, web
// session, personal workspace only; enforced by the backend).
export async function GET(request: NextRequest) {
  const limit = request.nextUrl.searchParams.get("limit");
  if (limit !== null && !/^[1-9][0-9]?$|^100$/.test(limit)) {
    return NextResponse.json({ detail: "limit must be a number from 1 to 100." }, { status: 400 });
  }
  return proxyGet(request, "/connectors/home-assistant/direct/calls", {
    required: ["workspace"],
    optional: ["limit"],
    uuids: ["workspace"],
  });
}
