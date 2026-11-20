import type { NextRequest } from "next/server";
import { notFound, proxyDelete, validSegments } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ termId: string }>;
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
  const { termId } = await params;
  if (!validSegments([termId])) return notFound();
  return proxyDelete(request, `/memory/ingestion/scrub-terms/${termId}`);
}
