import type { NextRequest } from "next/server";
import { proxyMutation } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Bulk approval: the backend refuses anything but pending work_log items (d6).
export async function POST(request: NextRequest) {
  return proxyMutation(request, "POST", "/memory/learning/bulk-approve");
}
