import type { NextRequest } from "next/server";
import { proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  return proxyGet(
    request,
    "/deploys/runs",
    { required: ["workspace"], optional: ["target"], uuids: ["workspace", "target"] },
    [],
  );
}
