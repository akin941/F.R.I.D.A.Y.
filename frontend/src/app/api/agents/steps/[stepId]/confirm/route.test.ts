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

function params(stepId: string) {
  return Promise.resolve({ stepId });
}

function postRequest(stepId: string, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/agents/steps/${stepId}/confirm`, {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
  });
}

describe("POST /api/agents/steps/[stepId]/confirm", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/agents/steps/[stepId]/confirm/route");
    const response = await POST(postRequest(VALID_ID, { origin: "https://evil.example" }), {
      params: params(VALID_ID),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-UUID stepId with 404 before touching cookies or the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/agents/steps/[stepId]/confirm/route");
    const response = await POST(postRequest("not-a-uuid"), { params: params("not-a-uuid") });

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/agents/steps/[stepId]/confirm/route");
    const response = await POST(postRequest(VALID_ID), { params: params(VALID_ID) });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the created task on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ id: "t1", title: "Buy milk" }), { status: 201 }),
      ),
    );

    const { POST } = await import("@/app/api/agents/steps/[stepId]/confirm/route");
    const response = await POST(postRequest(VALID_ID), { params: params(VALID_ID) });

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ id: "t1", title: "Buy milk" });
  });
});
