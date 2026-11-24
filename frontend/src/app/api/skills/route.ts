import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { proxyGet, proxyMutation, workspaceSuffix } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 12 D20: the workspace's instruction skills (owner, web session).
export async function GET(request: NextRequest) {
  return proxyGet(request, "/skills", {
    required: ["workspace"],
    optional: ["status"],
    uuids: ["workspace"],
  });
}

// The owner proposes a SKILL.md typed in the editor (stays proposed).
export async function POST(request: NextRequest) {
  const suffix = workspaceSuffix(request);
  if (suffix instanceof NextResponse) return suffix;
  return proxyMutation(request, "POST", `/skills${suffix}`);
}
