import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";
import { rejectCrossOrigin, rejectNonMultipart } from "@/lib/csrf";
import { traceHeaders } from "@/lib/voice-trace";

export const runtime = "nodejs";

/**
 * `POST /api/voice/transcriptions` (Phase 6 D1): proxies the utterance WAV
 * to Django. Defense-in-depth cap only -- the backend's streaming size guard
 * and WAV validation are the source of truth. 30 s of 16 kHz mono PCM16 is
 * 960 KB; this leaves room for multipart framing and a longer configured
 * `VOICE_MAX_UTTERANCE_SECONDS` without ever buffering an unbounded body.
 */
const MAX_PROXY_VOICE_UPLOAD_BYTES = 2_500_000;

function oversizedResponse() {
  return NextResponse.json(
    {
      detail: "Utterance exceeds the maximum length.",
      code: "voice.upload_too_large",
    },
    { status: 413 },
  );
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

  // Count real bytes off a clone before `formData()` buffers the body (a
  // chunked request has no trustworthy Content-Length) -- the knowledge
  // file-upload route's pattern.
  const sizeCheckReader = request.clone().body?.getReader();
  if (sizeCheckReader) {
    let received = 0;
    while (true) {
      const { done, value } = await sizeCheckReader.read();
      if (done) break;
      received += value.byteLength;
      if (received > MAX_PROXY_VOICE_UPLOAD_BYTES) {
        await sizeCheckReader.cancel();
        return oversizedResponse();
      }
    }
  }

  let formData: FormData;
  try {
    formData = await request.formData();
  } catch {
    return NextResponse.json(
      { detail: "Invalid multipart body." },
      { status: 400 },
    );
  }

  const response = await backendFetch("/voice/transcriptions", {
    method: "POST",
    accessToken,
    body: formData,
    headers: traceHeaders(request),
    signal: request.signal,
  });
  const body = await response.json().catch(() => ({}));
  const headers: Record<string, string> = { "Cache-Control": "no-store" };
  const retryAfter = response.headers.get("retry-after");
  if (retryAfter) headers["Retry-After"] = retryAfter;
  return NextResponse.json(body, { status: response.status, headers });
}
