import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

function cookieStore(accessToken?: string) {
  return {
    get: (name: string) =>
      name === "jarvis_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

function formDataRequest(formData: FormData, headers: Record<string, string> = {}) {
  // No explicit content-type header here (unless overridden): the runtime
  // computes the real `multipart/form-data; boundary=...` value itself from
  // the FormData body, exactly like a browser would.
  const request = new NextRequest("http://localhost:3001/api/knowledge/documents/file-upload", {
    method: "POST",
    headers: { origin: "http://localhost:3001", ...headers },
    body: formData,
  });
  // jsdom's Blob is not recognized by undici's own multipart re-parser
  // (`request.formData()` asserts `webidl.is.File`, which jsdom's File
  // instances fail here) — a jsdom-vs-undici test-environment mismatch, not
  // a route-code bug, since a real browser's File objects parse correctly
  // server-side in production. Overriding `.formData()` to resolve the
  // already-built FormData directly keeps the header-driven checks (origin,
  // Content-Type) genuine while sidestepping that environment gap for the
  // handful of tests that actually reach body parsing.
  request.formData = async () => formData;
  return request;
}

function sampleFormData() {
  const formData = new FormData();
  formData.set("workspace", "w1");
  formData.set("title", "Notes");
  formData.set("file", new Blob(["hello"], { type: "text/plain" }), "notes.txt");
  return formData;
}

describe("POST /api/knowledge/documents/file-upload", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/file-upload/route");
    const response = await POST(
      formDataRequest(sampleFormData(), { origin: "https://evil.example" }),
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-multipart Content-Type before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/file-upload/route");
    const response = await POST(
      formDataRequest(sampleFormData(), { "content-type": "application/json" }),
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/file-upload/route");
    const response = await POST(formDataRequest(sampleFormData()));

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the created document body and status from the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () =>
        new Response(JSON.stringify({ id: "d1", title: "Notes", status: "pending" }), {
          status: 201,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/file-upload/route");
    const response = await POST(formDataRequest(sampleFormData()));

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ id: "d1", title: "Notes", status: "pending" });
    // The outgoing call must forward a FormData body, not re-encode it as
    // JSON (which would strip the file entirely).
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("forwards the oversized-upload error from the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "File exceeds the 1000000-byte upload limit." }), {
            status: 400,
          }),
      ),
    );

    const { POST } = await import("@/app/api/knowledge/documents/file-upload/route");
    const response = await POST(formDataRequest(sampleFormData()));

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({
      detail: "File exceeds the 1000000-byte upload limit.",
    });
  });

  it("rejects an oversized body before ever calling formData() or the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { env } = await import("@/lib/env");
    const request = formDataRequest(sampleFormData());
    // jsdom's Blob doesn't serialize real bytes through undici's multipart
    // encoder in this test environment (the same jsdom-vs-undici mismatch
    // `formDataRequest` already works around for `.formData()` — here it
    // silently produces a near-empty body instead of throwing), so a real
    // oversized Blob can't be used to exercise the byte-counting logic
    // itself. Controlling exactly what the size-check's `.clone().body`
    // reader sees tests that logic directly and in isolation. Derived from
    // the actual configured limit (not a hardcoded number) so this test
    // can't silently drift out of sync with it.
    const oversizedStream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(env.KNOWLEDGE_MAX_UPLOAD_BYTES + 100_000));
        controller.close();
      },
    });
    request.clone = () => ({ body: oversizedStream }) as unknown as NextRequest;
    const formDataSpy = vi.fn(request.formData);
    request.formData = formDataSpy;

    const { POST } = await import("@/app/api/knowledge/documents/file-upload/route");
    const response = await POST(request);

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(formDataSpy).not.toHaveBeenCalled();
  });
});
