import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { workspaceSuffix } from "@/lib/bff-proxy";
import { rejectCrossOrigin, rejectNonMultipart } from "@/lib/csrf";

export const runtime = "nodejs";

// Phase 12 D13: a SKILL.md or a .zip bundle. Defense in depth only -- the
// backend's SKILLS_BUNDLE_MAX_BYTES (1 MB, also on the request body) is the
// source of truth; this proxy counts real bytes off the wire before parsing
// (multipart framing allowed on top).
const MAX_PROXY_SKILL_UPLOAD_BYTES = 1024 * 1024 + 64 * 1024;

export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonMultipart(request);
  if (contentTypeError) return contentTypeError;
  const suffix = workspaceSuffix(request);
  if (suffix instanceof NextResponse) return suffix;

  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  const reader = request.clone().body?.getReader();
  if (reader) {
    let received = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      received += value.byteLength;
      if (received > MAX_PROXY_SKILL_UPLOAD_BYTES) {
        await reader.cancel();
        return NextResponse.json(
          { detail: "The upload is too large.", code: "skill.bundle_too_large" },
          { status: 400 },
        );
      }
    }
  }

  let formData: FormData;
  try {
    formData = await request.formData();
  } catch {
    return NextResponse.json({ detail: "Invalid multipart body." }, { status: 400 });
  }
  const file = formData.get("file");
  if (!file || typeof file === "string") {
    return NextResponse.json({ detail: "A 'file' is required.", code: "invalid" }, { status: 400 });
  }
  const outgoing = new FormData();
  outgoing.set("file", file);
  const response = await backendFetch(`/skills/upload${suffix}`, {
    method: "POST",
    accessToken,
    body: outgoing,
  });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
