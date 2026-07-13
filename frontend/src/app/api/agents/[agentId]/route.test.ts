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

function params(agentId: string) {
  return Promise.resolve({ agentId });
}

function getRequest(agentId: string) {
  return new NextRequest(`http://localhost:3001/api/agents/${agentId}`, { method: "GET" });
}

const INJECTION_IDS = ["../ai/conversations", "not-a-uuid", "11111111-1111-1111-1111-11111111111g"];

describe("GET /api/agents/[agentId]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID agentId (%s) with 404 before touching cookies or the backend",
    async (agentId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { GET } = await import("@/app/api/agents/[agentId]/route");
      const response = await GET(getRequest(agentId), { params: params(agentId) });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/agents/[agentId]/route");
    const response = await GET(getRequest(VALID_ID), { params: params(VALID_ID) });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the agent detail body on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ id: VALID_ID, goal: "Do things", steps: [] }), {
            status: 200,
          }),
      ),
    );

    const { GET } = await import("@/app/api/agents/[agentId]/route");
    const response = await GET(getRequest(VALID_ID), { params: params(VALID_ID) });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ id: VALID_ID, goal: "Do things", steps: [] });
  });
});
