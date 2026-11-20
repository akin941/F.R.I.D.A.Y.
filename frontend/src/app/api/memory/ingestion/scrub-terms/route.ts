import type { NextRequest } from "next/server";
import { proxyGet, proxyMutation } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  return proxyGet(
    request,
    "/memory/ingestion/scrub-terms",
    { required: ["workspace"], uuids: ["workspace"] },
    [],
  );
}

// A new scrub term: body {workspace, term}.
export async function POST(request: NextRequest) {
  return proxyMutation(request, "POST", "/memory/ingestion/scrub-terms");
}
