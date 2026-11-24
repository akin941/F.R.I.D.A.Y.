import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { proxyDelete, proxyGet, proxyMutation, workspaceSuffix } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 12 D3: "Proaktif çalışmaları duraklat" -- read, pause, resume.
export async function GET(request: NextRequest) {
  return proxyGet(request, "/proactive/pause", { required: ["workspace"], uuids: ["workspace"] });
}

export async function POST(request: NextRequest) {
  const suffix = workspaceSuffix(request);
  if (suffix instanceof NextResponse) return suffix;
  return proxyMutation(request, "POST", `/proactive/pause${suffix}`, { withBody: false });
}

export async function DELETE(request: NextRequest) {
  const suffix = workspaceSuffix(request);
  if (suffix instanceof NextResponse) return suffix;
  return proxyDelete(request, `/proactive/pause${suffix}`);
}
