import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 12 D16: the latest weekly curator run and its open archive
// suggestions (owner, web session). Read-only.
export async function GET(request: NextRequest) {
  return proxyGet(request, "/skills/curator", {
    required: ["workspace"],
    uuids: ["workspace"],
  });
}
