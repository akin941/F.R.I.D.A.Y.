import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

const VALID_ID = "11111111-1111-1111-1111-111111111111";

function cookieStore(accessToken?: string) {
  return {
    get: (name: string) =>
      name === "friday_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

function params(action: string, memoryId: string = VALID_ID) {
  return Promise.resolve({ memoryId, action });
}

function postRequest(body?: unknown, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/memories/${VALID_ID}/approve`, {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

describe("POST /api/memories/[memoryId]/[action]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking the action, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/memories/[memoryId]/[action]/route");
    const response = await POST(postRequest(undefined, { origin: "https://evil.example" }), {
      params: params("approve"),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before checking the action, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/memories/[memoryId]/[action]/route");
    const response = await POST(postRequest(undefined, { "content-type": "text/plain" }), {
      params: params("approve"),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects an unknown action with 404 before touching cookies or the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/memories/[memoryId]/[action]/route");
    const response = await POST(postRequest(), { params: params("promote") });

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it.each(["../ai/conversations", "not-a-uuid"])(
    "rejects a non-UUID memoryId (%s) with 404 before touching cookies or the backend",
    async (memoryId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { POST } = await import("@/app/api/memories/[memoryId]/[action]/route");
      const response = await POST(postRequest(), { params: params("approve", memoryId) });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/memories/[memoryId]/[action]/route");
    const response = await POST(postRequest(), { params: params("approve") });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["reject", "restore", "forget"])(
    "forwards an empty body as {} for %s",
    async (action) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
        async () => new Response(JSON.stringify({ id: "m1", status: action }), { status: 200 }),
      );
      vi.stubGlobal("fetch", fetchMock);

      const { POST } = await import("@/app/api/memories/[memoryId]/[action]/route");
      const response = await POST(postRequest(), { params: params(action) });

      expect(response.status).toBe(200);
      const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(url).toContain(`/memories/${VALID_ID}/${action}`);
      expect(options.body).toBe("{}");
    },
  );

  it("forwards curated fields for approve", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify({ id: "m1", status: "approved" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/memories/[memoryId]/[action]/route");
    const response = await POST(postRequest({ sensitivity: "private" }), {
      params: params("approve"),
    });

    expect(response.status).toBe(200);
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(options.body as string)).toEqual({ sensitivity: "private" });
  });
});
