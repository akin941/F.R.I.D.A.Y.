import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Coding sessions (Phase 8A D12): every workspace the owner is in, or one.
export async function GET(request: NextRequest) {
  return proxyGet(request, "/memory/sessions", { optional: ["workspace", "limit"], uuids: ["workspace"] }, []);
}
