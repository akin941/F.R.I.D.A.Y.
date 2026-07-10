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

function postRequest(body: unknown, headers: Record<string, string> = {}) {
  return new NextRequest("http://localhost:3001/api/conversations/c1/messages", {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

function params() {
  return Promise.resolve({ conversationId: "c1" });
}

describe("POST /api/conversations/[conversationId]/messages", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/conversations/[conversationId]/messages/route");
    const response = await POST(postRequest({ content: "hi" }, { origin: "https://evil.example" }), {
      params: params(),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/conversations/[conversationId]/messages/route");
    const response = await POST(postRequest({ content: "hi" }, { "content-type": "text/plain" }), {
      params: params(),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/conversations/[conversationId]/messages/route");
    const response = await POST(postRequest({ content: "hi" }), { params: params() });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("streams the upstream SSE body through untouched on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const encoder = new TextEncoder();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode('data: {"type":"chunk","text":"hi"}\n\n'));
              controller.close();
            },
          }),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
    );

    const { POST } = await import("@/app/api/conversations/[conversationId]/messages/route");
    const response = await POST(postRequest({ content: "hi" }), { params: params() });

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("text/event-stream");
    const text = await response.text();
    expect(text).toContain('"type":"chunk"');
  });

  it("forwards the Retry-After header on a 429 from the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "throttled" }), {
          status: 429,
          headers: { "Retry-After": "12" },
        }),
      ),
    );

    const { POST } = await import("@/app/api/conversations/[conversationId]/messages/route");
    const response = await POST(postRequest({ content: "hi" }), { params: params() });

    expect(response.status).toBe(429);
    expect(response.headers.get("Retry-After")).toBe("12");
  });
});
