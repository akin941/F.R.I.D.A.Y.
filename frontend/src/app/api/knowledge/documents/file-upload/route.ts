import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonMultipart } from "@/lib/csrf";
import { env } from "@/lib/env";

export const runtime = "nodejs";

// Defense-in-depth cap for this proxy itself (not the source of truth — the
// backend's KNOWLEDGE_MAX_UPLOAD_BYTES / _MaxSizeUploadHandler is). Read from
// env (see env.ts) rather than hardcoded, so raising the backend's limit in a
// deployment doesn't silently start rejecting valid uploads here first
// (round-2 Codex finding).
const MAX_PROXY_UPLOAD_BYTES = env.KNOWLEDGE_MAX_UPLOAD_BYTES;

function oversizedResponse() {
  return NextResponse.json({ detail: "File exceeds the upload size limit." }, { status: 400 });
}

export async function POST(request: NextRequest) {
  const originError = rejectCrossOrigin(request);
  if (originError) return originError;
  const contentTypeError = rejectNonMultipart(request);
  if (contentTypeError) return contentTypeError;

  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  // Count real bytes off a teed clone before ever calling `request.formData()`
  // on the original — `formData()` fully parses/buffers the multipart body,
  // so without this an oversized upload would already be materialized by the
  // time any limit could apply (Codex finding). A `Content-Length` header
  // check alone would not be enough: a chunked-transfer-encoded request has
  // none, the exact gap the backend's own `_MaxSizeUploadHandler` was built
  // to close for the same reason — so this counts bytes off the wire
  // instead of trusting a client-supplied header.
  const sizeCheckReader = request.clone().body?.getReader();
  if (sizeCheckReader) {
    let received = 0;
    while (true) {
      const { done, value } = await sizeCheckReader.read();
      if (done) break;
      received += value.byteLength;
      if (received > MAX_PROXY_UPLOAD_BYTES) {
        await sizeCheckReader.cancel();
        return oversizedResponse();
      }
    }
  }

  let formData: FormData;
  try {
    formData = await request.formData();
  } catch {
    return NextResponse.json({ detail: "Invalid multipart body." }, { status: 400 });
  }

  // Re-sent as a fresh FormData: `backendFetch` (and, transitively, `fetch`)
  // computes its own multipart boundary from this object, independent of
  // whatever boundary the original browser request used.
  const response = await backendFetch("/knowledge/documents/file-upload", {
    method: "POST",
    accessToken,
    body: formData,
  });
  const body = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}
