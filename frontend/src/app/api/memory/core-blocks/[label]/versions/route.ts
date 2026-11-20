import type { NextRequest } from "next/server";
import { isCoreBlockLabel, notFound, proxyGet } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ label: string }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  const { label } = await params;
  if (!isCoreBlockLabel(label)) return notFound();
  return proxyGet(
    request,
    `/memory/core-blocks/${label}/versions`,
    { required: ["workspace"], uuids: ["workspace"] },
    [],
  );
}
