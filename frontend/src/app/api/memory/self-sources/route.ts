import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// The read-only `self` block's source anchors (Phase 8A D8).
export async function GET(request: NextRequest) {
  return proxyGet(request, "/ai/self-sources");
}
