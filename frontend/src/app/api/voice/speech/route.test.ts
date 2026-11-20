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
const WAV = new Uint8Array([
  0x52, 0x49, 0x46, 0x46, 1, 2, 3, 4, 0x57, 0x41, 0x56, 0x45,
]);

function jsonRequest(body: unknown, headers: Record<string, string> = {}) {
  return new NextRequest("http://localhost:3001/api/voice/speech", {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

const BODY = { conversation: "c1", text: "Merhaba." };

describe("POST /api/voice/speech", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before auth or the backend", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/voice/speech/route");
    const response = await POST(
      jsonRequest(BODY, { origin: "https://evil.example" }),
    );
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before auth or the backend", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/voice/speech/route");
    const response = await POST(
      jsonRequest(BODY, { "content-type": "text/plain" }),
    );
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns 401 without a session", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/voice/speech/route");
    expect((await POST(jsonRequest(BODY))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("streams the WAV back unmodified and forwards the trace id", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<
      (input: string, init?: RequestInit) => Promise<Response>
    >(
      async () =>
        new Response(WAV, {
          status: 200,
          headers: {
            "content-type": "audio/wav",
            "content-length": String(WAV.byteLength),
          },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/voice/speech/route");
    const response = await POST(jsonRequest(BODY, { "x-friday-trace": TRACE }));
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("audio/wav");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(WAV);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/voice\/speech$/);
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Friday-Trace"]).toBe(TRACE);
    expect(headers.Accept).toBe("audio/wav");
    expect(JSON.parse(init.body as string)).toEqual(BODY);
  });

  it("forwards a backend error body and status", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ code: "voice.no_local_provider" }), {
            status: 503,
            headers: { "content-type": "application/json" },
          }),
      ),
    );
    const { POST } = await import("@/app/api/voice/speech/route");
    const response = await POST(jsonRequest(BODY));
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ code: "voice.no_local_provider" });
  });

  it("never passes a non-WAV success body on as audio", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("<html>", {
            status: 200,
            headers: { "content-type": "text/html" },
          }),
      ),
    );
    const { POST } = await import("@/app/api/voice/speech/route");
    const response = await POST(jsonRequest(BODY));
    expect(response.status).toBe(502);
  });
});
