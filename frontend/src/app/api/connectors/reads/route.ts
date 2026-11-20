import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Recent connector reads (bookkeeping from the audit trail, never content).
export async function GET(request: NextRequest) {
  return proxyGet(
    request,
    "/connectors/reads",
    { required: ["workspace"], optional: ["connector"], uuids: ["workspace"] },
    [],
  );
}
