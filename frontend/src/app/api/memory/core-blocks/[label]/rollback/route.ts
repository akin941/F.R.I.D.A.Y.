import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { isCoreBlockLabel, notFound, proxyMutation, workspaceSuffix } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ label: string }>;
}

// Rollback = a new version with an old version's content: body {version}.
export async function POST(request: NextRequest, { params }: RouteParams) {
  const { label } = await params;
  if (!isCoreBlockLabel(label)) return notFound();
  const suffix = workspaceSuffix(request);
  if (suffix instanceof NextResponse) return suffix;
  return proxyMutation(request, "POST", `/memory/core-blocks/${label}/rollback${suffix}`);
}
