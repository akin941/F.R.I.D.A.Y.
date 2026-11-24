import type { NextRequest } from "next/server";
import { proxyMutation } from "@/lib/bff-proxy";

export const runtime = "nodejs";

// Phase 12 D16: the owner dismisses one curator archive suggestion
// (`{"run_id", "skill_id"}`, validated by the backend). The skill itself is
// untouched; archiving it is the separate archive route.
export async function POST(request: NextRequest) {
  return proxyMutation(request, "POST", "/skills/curator/dismiss");
}
