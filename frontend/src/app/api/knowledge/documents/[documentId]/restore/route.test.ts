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
      name === "jarvis_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

function postRequest(headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/knowledge/documents/${VALID_ID}/restore`, {
    method: "POST",
    headers: { origin: "http://localhost:3001", "content-type": "application/json", ...headers },
  });
}

function params(documentId: string = VALID_ID) {
  return { params: Promise.resolve({ documentId }) };
}

describe("POST /api/knowledge/documents/[documentId]/restore", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a cross-origin request before the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/[documentId]/restore/route");
    const response = await POST(postRequest({ origin: "https://evil.example" }), params());

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["../ai/conversations", "not-a-uuid"])(
    "rejects a non-UUID id (%s) before the backend",
    async (documentId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { POST } = await import("@/app/api/knowledge/documents/[documentId]/restore/route");
      const response = await POST(postRequest(), params(documentId));

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("returns 401 without an access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/[documentId]/restore/route");
    expect((await POST(postRequest(), params())).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the restore and the backend's answer", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify({ id: VALID_ID, deleted_at: null }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/[documentId]/restore/route");
    const response = await POST(postRequest(), params());

    const [calledUrl, init] = fetchMock.mock.calls[0] ?? [];
    expect(calledUrl).toMatch(new RegExp(`/knowledge/documents/${VALID_ID}/restore$`));
    expect(init?.method).toBe("POST");
    expect(response.status).toBe(200);
  });
});
