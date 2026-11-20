import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// The owner's ingestion sources (Phase 8A D11), listed per workspace.
export async function GET(request: NextRequest) {
  return proxyGet(request, "/memory/ingestion/sources", { required: ["workspace"], uuids: ["workspace"] }, []);
}
