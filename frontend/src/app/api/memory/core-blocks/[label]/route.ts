import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { isCoreBlockLabel, notFound, proxyGet, proxyMutation, workspaceSuffix } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ label: string }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { label } = await params;
  if (!isCoreBlockLabel(label)) return notFound();
  return proxyGet(request, `/memory/core-blocks/${label}`, {
    required: ["workspace"],
    uuids: ["workspace"],
  });
}

// Owner edit: body {content, sensitivity?, base_version?}. `self` is refused by the backend.
export async function PATCH(request: NextRequest, { params }: RouteParams) {
  const { label } = await params;
  if (!isCoreBlockLabel(label)) return notFound();
  const suffix = workspaceSuffix(request);
  if (suffix instanceof NextResponse) return suffix;
  return proxyMutation(request, "PATCH", `/memory/core-blocks/${label}${suffix}`);
}
