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
  return new NextRequest("http://localhost:3001/api/knowledge/documents/url", {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

describe("POST /api/knowledge/documents/url", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/url/route");
    const response = await POST(
      postRequest({ workspace: "w1" }, { origin: "https://evil.example" }),
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/url/route");
    const response = await POST(postRequest({ workspace: "w1" }, { "content-type": "text/plain" }));

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/url/route");
    const response = await POST(postRequest({ workspace: "w1" }));

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the created (pending) document body and status from the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ id: "d1", status: "pending", source_type: "url" }), {
            status: 201,
          }),
      ),
    );

    const { POST } = await import("@/app/api/knowledge/documents/url/route");
    const response = await POST(
      postRequest({ workspace: "w1", title: "Article", source_url: "https://example.com/a" }),
    );

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ id: "d1", status: "pending", source_type: "url" });
  });

  it("forwards a field-keyed validation error from the backend as-is", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ source_url: ["Unsupported URL scheme: 'ftp'."] }), {
            status: 400,
          }),
      ),
    );

    const { POST } = await import("@/app/api/knowledge/documents/url/route");
    const response = await POST(
      postRequest({ workspace: "w1", title: "Article", source_url: "ftp://example.com/a" }),
    );

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ source_url: ["Unsupported URL scheme: 'ftp'."] });
  });
});
