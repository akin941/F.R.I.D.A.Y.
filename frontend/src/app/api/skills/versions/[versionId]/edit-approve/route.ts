import type { NextRequest } from "next/server";
import { notFound, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ versionId: string }>;
}

// Phase 12 D14: edit a pending version and approve the edit.
export async function POST(request: NextRequest, { params }: RouteParams) {
  const { versionId } = await params;
  if (!validSegments([versionId])) return notFound();
  return proxyMutation(request, "POST", `/skills/versions/${versionId}/edit-approve`);
}
