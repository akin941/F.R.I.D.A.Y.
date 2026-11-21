import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 8B branch 9: the eval runs' metrics (branch 3's owner-only,
// `for_actor()`-scoped `GET /memory/evals/runs`). Never case text.
export async function GET(request: NextRequest) {
  return proxyGet(request, "/memory/evals/runs", {}, []);
}
