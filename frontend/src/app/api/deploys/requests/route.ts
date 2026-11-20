import type { NextRequest } from "next/server";
import { proxyMutation } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// The owner's "Deploy" click: mints the pending `deploy` gate {target, sha}.
// The decision itself is a separate step on the approvals surface.
export async function POST(request: NextRequest) {
  return proxyMutation(request, "POST", "/deploys/requests");
}
