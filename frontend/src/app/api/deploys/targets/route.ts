import type { NextRequest } from "next/server";
import { proxyGet, proxyMutation } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Firma → "Deploy hedefleri" (Phase 5 branch 9).
export async function GET(request: NextRequest) {
  return proxyGet(
    request,
    "/deploys/targets",
    { required: ["workspace"], uuids: ["workspace"] },
    [],
  );
}

export async function POST(request: NextRequest) {
  return proxyMutation(request, "POST", "/deploys/targets");
}
