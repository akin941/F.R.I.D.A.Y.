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

function params(conversationId: string = VALID_ID) {
  return Promise.resolve({ conversationId });
}

function jsonRequest(method: string, body?: unknown, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/conversations/${VALID_ID}`, {
    method,
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

// Same discipline as the memories/[memoryId] route: an unvalidated dynamic
// segment interpolated into a server-side, bearer-authenticated fetch could
// redirect the upstream request to an unintended backend path.
const INJECTION_IDS = ["../ai/conversations", "not-a-uuid", "11111111-1111-1111-1111-11111111111g"];

describe("GET /api/conversations/[conversationId]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/conversations/[conversationId]/route");
    const response = await GET(jsonRequest("GET"), { params: params() });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards a 404 from the backend for an out-of-scope conversation", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 404 })),
    );

    const { GET } = await import("@/app/api/conversations/[conversationId]/route");
    const response = await GET(jsonRequest("GET"), { params: params() });

    expect(response.status).toBe(404);
  });

  it("forwards the conversation including the private flag on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ id: VALID_ID, private: false }), { status: 200 }),
      ),
    );

    const { GET } = await import("@/app/api/conversations/[conversationId]/route");
    const response = await GET(jsonRequest("GET"), { params: params() });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ id: VALID_ID, private: false });
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID conversationId (%s) with 404 before touching cookies or the backend",
    async (conversationId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { GET } = await import("@/app/api/conversations/[conversationId]/route");
      const response = await GET(jsonRequest("GET"), { params: params(conversationId) });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );
});

describe("PATCH /api/conversations/[conversationId]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before calling the backend or reading cookies", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { PATCH } = await import("@/app/api/conversations/[conversationId]/route");
    const response = await PATCH(
      jsonRequest("PATCH", { private: true }, { origin: "https://evil.example" }),
      { params: params() },
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before calling the backend or reading cookies", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { PATCH } = await import("@/app/api/conversations/[conversationId]/route");
    const response = await PATCH(
      jsonRequest("PATCH", { private: true }, { "content-type": "text/plain" }),
      { params: params() },
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { PATCH } = await import("@/app/api/conversations/[conversationId]/route");
    const response = await PATCH(jsonRequest("PATCH", { private: true }), { params: params() });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the updated conversation on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ id: VALID_ID, private: true }), { status: 200 }),
      ),
    );

    const { PATCH } = await import("@/app/api/conversations/[conversationId]/route");
    const response = await PATCH(jsonRequest("PATCH", { private: true }), { params: params() });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ id: VALID_ID, private: true });
  });

  it("forwards a 400 for an attempt to unset private (one-way)", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ private: ["private can only be set to true."] }), {
            status: 400,
          }),
      ),
    );

    const { PATCH } = await import("@/app/api/conversations/[conversationId]/route");
    const response = await PATCH(jsonRequest("PATCH", { private: false }), { params: params() });

    expect(response.status).toBe(400);
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID conversationId (%s) with 404 before touching cookies or the backend",
    async (conversationId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { PATCH } = await import("@/app/api/conversations/[conversationId]/route");
      const response = await PATCH(jsonRequest("PATCH", { private: true }), {
        params: params(conversationId),
      });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );
});
