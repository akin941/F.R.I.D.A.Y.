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

function params(documentId: string = VALID_ID) {
  return Promise.resolve({ documentId });
}

function jsonRequest(method: string, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/knowledge/documents/${VALID_ID}`, {
    method,
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
  });
}

// Same class of guard as memories/[memoryId]: an unvalidated dynamic segment
// interpolated into a server-side, bearer-authenticated fetch could redirect
// the upstream request to an unintended backend path.
const INJECTION_IDS = ["../ai/conversations", "not-a-uuid", "11111111-1111-1111-1111-11111111111g"];

describe("GET /api/knowledge/documents/[documentId]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/knowledge/documents/[documentId]/route");
    const response = await GET(jsonRequest("GET"), { params: params() });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards a 404 from the backend for an out-of-scope document", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 404 })),
    );

    const { GET } = await import("@/app/api/knowledge/documents/[documentId]/route");
    const response = await GET(jsonRequest("GET"), { params: params() });

    expect(response.status).toBe(404);
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID documentId (%s) with 404 before touching cookies or the backend",
    async (documentId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { GET } = await import("@/app/api/knowledge/documents/[documentId]/route");
      const response = await GET(jsonRequest("GET"), { params: params(documentId) });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );
});

describe("DELETE /api/knowledge/documents/[documentId]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before calling the backend or reading cookies", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { DELETE } = await import("@/app/api/knowledge/documents/[documentId]/route");
    const response = await DELETE(jsonRequest("DELETE", { origin: "https://evil.example" }), {
      params: params(),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before calling the backend or reading cookies", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { DELETE } = await import("@/app/api/knowledge/documents/[documentId]/route");
    const response = await DELETE(jsonRequest("DELETE", { "content-type": "text/plain" }), {
      params: params(),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { DELETE } = await import("@/app/api/knowledge/documents/[documentId]/route");
    const response = await DELETE(jsonRequest("DELETE"), { params: params() });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns 204 with no body on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 204 })),
    );

    const { DELETE } = await import("@/app/api/knowledge/documents/[documentId]/route");
    const response = await DELETE(jsonRequest("DELETE"), { params: params() });

    expect(response.status).toBe(204);
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID documentId (%s) with 404 before touching cookies or the backend",
    async (documentId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { DELETE } = await import("@/app/api/knowledge/documents/[documentId]/route");
      const response = await DELETE(jsonRequest("DELETE"), { params: params(documentId) });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );
});
