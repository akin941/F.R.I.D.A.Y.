import type { NextRequest } from "next/server";
import { proxyFacadeRequest } from "@/lib/openai-facade-proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** OpenAI-compatible `GET /v1/models` (Phase 4 branch 6): bearer-only passthrough. */
export async function GET(request: NextRequest) {
  return proxyFacadeRequest(request, "/openai/models");
}
