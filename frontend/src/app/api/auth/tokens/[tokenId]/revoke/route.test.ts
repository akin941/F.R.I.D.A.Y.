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

function params(tokenId: string = VALID_ID) {
  return Promise.resolve({ tokenId });
}

function postRequest(headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/auth/tokens/${VALID_ID}/revoke`, {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
  });
}

const INJECTION_IDS = ["../ai/conversations", "not-a-uuid", "11111111-1111-1111-1111-11111111111g"];

describe("POST /api/auth/tokens/[tokenId]/revoke", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/auth/tokens/[tokenId]/revoke/route");
    const response = await POST(postRequest({ origin: "https://evil.example" }), { params: params() });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/auth/tokens/[tokenId]/revoke/route");
    const response = await POST(postRequest({ "content-type": "text/plain" }), { params: params() });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID tokenId (%s) with 404 before touching cookies or the backend",
    async (tokenId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { POST } = await import("@/app/api/auth/tokens/[tokenId]/revoke/route");
      const response = await POST(postRequest(), { params: params(tokenId) });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/auth/tokens/[tokenId]/revoke/route");
    const response = await POST(postRequest(), { params: params() });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the revoked token body from the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ id: VALID_ID, revoked_at: "2026-07-13T00:00:00Z" }), {
            status: 200,
          }),
      ),
    );

    const { POST } = await import("@/app/api/auth/tokens/[tokenId]/revoke/route");
    const response = await POST(postRequest(), { params: params() });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ id: VALID_ID, revoked_at: "2026-07-13T00:00:00Z" });
  });
});
