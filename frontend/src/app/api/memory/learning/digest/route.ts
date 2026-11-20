import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// The morning card (Phase 8A D10): owner, web session; authorization stays in the backend.
export async function GET(request: NextRequest) {
  return proxyGet(request, "/memory/learning/digest", { optional: ["days"] });
}
