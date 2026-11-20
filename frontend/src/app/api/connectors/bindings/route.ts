import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Settings → "Bağlantılar" (Phase 5 branch 9): the workspace's connectors.
// Owner + trusted web session are enforced by the backend.
export async function GET(request: NextRequest) {
  return proxyGet(
    request,
    "/connectors/bindings",
    { required: ["workspace"], uuids: ["workspace"] },
    [],
  );
}
