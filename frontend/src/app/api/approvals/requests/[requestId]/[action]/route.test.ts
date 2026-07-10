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

function params(requestId: string, action: string) {
  return Promise.resolve({ requestId, action });
}

function postRequest(requestId: string, action: string, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/approvals/requests/${requestId}/${action}`, {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
  });
}

const INJECTION_IDS = ["../ai/conversations", "not-a-uuid", "11111111-1111-1111-1111-11111111111g"];

describe("POST /api/approvals/requests/[requestId]/[action]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/approvals/requests/[requestId]/[action]/route");
    const response = await POST(
      postRequest(VALID_ID, "approve", { origin: "https://evil.example" }),
      { params: params(VALID_ID, "approve") },
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/approvals/requests/[requestId]/[action]/route");
    const response = await POST(
      postRequest(VALID_ID, "approve", { "content-type": "text/plain" }),
      { params: params(VALID_ID, "approve") },
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 404 for an unknown action before touching cookies or the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/approvals/requests/[requestId]/[action]/route");
    const response = await POST(postRequest(VALID_ID, "promote"), {
      params: params(VALID_ID, "promote"),
    });

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID requestId (%s) with 404 before touching cookies or the backend",
    async (requestId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { POST } = await import("@/app/api/approvals/requests/[requestId]/[action]/route");
      const response = await POST(postRequest(requestId, "approve"), {
        params: params(requestId, "approve"),
      });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/approvals/requests/[requestId]/[action]/route");
    const response = await POST(postRequest(VALID_ID, "approve"), {
      params: params(VALID_ID, "approve"),
    });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the decided request on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ id: "r1", status: "approved" }), { status: 200 }),
      ),
    );

    const { POST } = await import("@/app/api/approvals/requests/[requestId]/[action]/route");
    const response = await POST(postRequest(VALID_ID, "approve"), {
      params: params(VALID_ID, "approve"),
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ id: "r1", status: "approved" });
  });

  it("forwards a 403 for a requester trying to decide their own request", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "The requester cannot decide their own request." }), {
            status: 403,
          }),
      ),
    );

    const { POST } = await import("@/app/api/approvals/requests/[requestId]/[action]/route");
    const response = await POST(postRequest(VALID_ID, "approve"), {
      params: params(VALID_ID, "approve"),
    });

    expect(response.status).toBe(403);
  });
});
