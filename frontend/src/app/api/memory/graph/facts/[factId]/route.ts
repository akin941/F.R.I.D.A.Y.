import type { NextRequest } from "next/server";
import { notFound, proxyDelete, proxyGet, proxyMutation, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ factId: string }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { factId } = await params;
  if (!validSegments([factId])) return notFound();
  return proxyGet(request, `/memory/graph/facts/${factId}`);
}

// An owner edit: a new version that supersedes the old one (never in place).
export async function PATCH(request: NextRequest, { params }: RouteParams) {
  const { factId } = await params;
  if (!validSegments([factId])) return notFound();
  return proxyMutation(request, "PATCH", `/memory/graph/facts/${factId}`);
}

// Owner trash (ADR-009): reversible.
export async function DELETE(request: NextRequest, { params }: RouteParams) {
  const { factId } = await params;
  if (!validSegments([factId])) return notFound();
  return proxyDelete(request, `/memory/graph/facts/${factId}`);
}
