import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

function cookieStore(accessToken?: string) {
  return {
    get: (name: string) =>
      name === "friday_access" && accessToken
        ? { name, value: accessToken }
        : undefined,
  };
}

const TRACE = "6f1c2d3e-4b5a-4c6d-8e7f-0123456789ab";

function formDataRequest(
  formData: FormData,
  headers: Record<string, string> = {},
) {
  const request = new NextRequest(
    "http://localhost:3001/api/voice/transcriptions",
    {
      method: "POST",
      headers: { origin: "http://localhost:3001", ...headers },
      body: formData,
    },
  );
  // Same jsdom-vs-undici workaround as the knowledge file-upload route test.
  request.formData = async () => formData;
  return request;
}

function sampleFormData() {
  const formData = new FormData();
  formData.set("conversation", "c1");
  formData.set(
    "audio",
    new Blob(["RIFF"], { type: "audio/wav" }),
    "utterance.wav",
  );
  return formData;
}

describe("POST /api/voice/transcriptions", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before auth or the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/voice/transcriptions/route");
    const response = await POST(
      formDataRequest(sampleFormData(), { origin: "https://evil.example" }),
    );
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-multipart Content-Type before auth or the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/voice/transcriptions/route");
    const response = await POST(
      formDataRequest(sampleFormData(), { "content-type": "application/json" }),
    );
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/voice/transcriptions/route");
    const response = await POST(formDataRequest(sampleFormData()));
    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the FormData body and a valid trace id, and returns the transcript", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<
      (input: string, init?: RequestInit) => Promise<Response>
    >(
      async () =>
        new Response(
          JSON.stringify({
            text: "merhaba",
            audio_seconds: 1.2,
            language: "tr",
          }),
          {
            status: 200,
          },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/voice/transcriptions/route");
    const response = await POST(
      formDataRequest(sampleFormData(), { "x-friday-trace": TRACE }),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      text: "merhaba",
      audio_seconds: 1.2,
      language: "tr",
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/voice\/transcriptions$/);
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.headers as Record<string, string>)["X-Friday-Trace"]).toBe(
      TRACE,
    );
  });

  it("drops a malformed trace header", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<
      (input: string, init?: RequestInit) => Promise<Response>
    >(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/voice/transcriptions/route");
    await POST(
      formDataRequest(sampleFormData(), { "x-friday-trace": "not-a-uuid" }),
    );
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(
      (init.headers as Record<string, string>)["X-Friday-Trace"],
    ).toBeUndefined();
  });

  it("forwards backend error codes and Retry-After", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ code: "voice.budget_exceeded" }), {
            status: 429,
            headers: { "retry-after": "30" },
          }),
      ),
    );
    const { POST } = await import("@/app/api/voice/transcriptions/route");
    const response = await POST(formDataRequest(sampleFormData()));
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("30");
    expect(await response.json()).toEqual({ code: "voice.budget_exceeded" });
  });

  it("rejects an oversized body before formData() or the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const request = formDataRequest(sampleFormData());
    const oversizedStream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(2_600_000));
        controller.close();
      },
    });
    request.clone = () => ({ body: oversizedStream }) as unknown as NextRequest;
    const formDataSpy = vi.fn(request.formData);
    request.formData = formDataSpy;
    const { POST } = await import("@/app/api/voice/transcriptions/route");
    const response = await POST(request);
    expect(response.status).toBe(413);
    expect((await response.json()).code).toBe("voice.upload_too_large");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(formDataSpy).not.toHaveBeenCalled();
  });
});
