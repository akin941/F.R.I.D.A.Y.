import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { proxyMutation, workspaceSuffix } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 12 D8: the owner adds an alias for one wake route.
export async function POST(request: NextRequest) {
  const suffix = workspaceSuffix(request);
  if (suffix instanceof NextResponse) return suffix;
  return proxyMutation(request, "POST", `/assistant/wake-routes/aliases${suffix}`);
}
