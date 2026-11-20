import type { NextRequest } from "next/server";
import { isConnectorName, notFound, proxyMutation } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteParams {
  params: Promise<{ connector: string }>;
}

// Switch one connector on/off for a workspace: body {workspace, enabled}.
export async function PUT(request: NextRequest, { params }: RouteParams) {
  const { connector } = await params;
  if (!isConnectorName(connector)) return notFound();
  return proxyMutation(request, "PUT", `/connectors/bindings/${connector}`);
}
