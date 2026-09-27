import type { NextRequest } from "next/server";
import { proxyFacadeRequest } from "@/lib/openai-facade-proxy";

export const runtime = "nodejs";
// Never cached or prerendered: every call is an authenticated completion.
export const dynamic = "force-dynamic";

/**
 * OpenAI-compatible `POST /v1/chat/completions` (Phase 4 branch 6):
 * bearer-only passthrough to Django's facade (lib/openai-facade-proxy.ts).
 * No Origin/Content-Type CSRF check on purpose: no cookie is ever read, so
 * a cross-site browser request carries no credential this route honors.
 */
export async function POST(request: NextRequest) {
  return proxyFacadeRequest(request, "/openai/chat/completions");
}
