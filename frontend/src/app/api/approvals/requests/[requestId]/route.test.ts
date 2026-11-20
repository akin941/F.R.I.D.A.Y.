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

function getRequest() {
  return new NextRequest(`http://localhost:3001/api/approvals/requests/${VALID_ID}`);
}

describe("GET /api/approvals/requests/[requestId]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without an access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/approvals/requests/[requestId]/route");
    const response = await GET(getRequest(), { params: Promise.resolve({ requestId: VALID_ID }) });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["../ai/conversations", "not-a-uuid"])(
    "rejects a non-UUID id (%s) before the backend",
    async (requestId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { GET } = await import("@/app/api/approvals/requests/[requestId]/route");
      const response = await GET(getRequest(), { params: Promise.resolve({ requestId }) });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("forwards the lookup with the bearer token and the backend's status", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify({ code: "not_found" }), { status: 404 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/approvals/requests/[requestId]/route");
    const response = await GET(getRequest(), { params: Promise.resolve({ requestId: VALID_ID }) });

    const [calledUrl, init] = fetchMock.mock.calls[0] ?? [];
    expect(calledUrl).toMatch(new RegExp(`/approvals/requests/${VALID_ID}$`));
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
    expect(response.status).toBe(404);
  });
});
