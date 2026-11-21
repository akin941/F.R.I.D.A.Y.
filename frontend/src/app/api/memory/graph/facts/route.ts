import type { NextRequest } from "next/server";
import { proxyGet, proxyMutation } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Facts of one workspace (filters: status, as_of, current).
export async function GET(request: NextRequest) {
  return proxyGet(
    request,
    "/memory/graph/facts",
    { required: ["workspace"], optional: ["status", "as_of", "current"], uuids: ["workspace"] },
    [],
  );
}

// An owner-typed fact (born approved, the owner's own words).
export async function POST(request: NextRequest) {
  return proxyMutation(request, "POST", "/memory/graph/facts");
}
