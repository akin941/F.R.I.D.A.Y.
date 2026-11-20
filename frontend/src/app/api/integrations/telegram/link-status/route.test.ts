import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

function cookieStore(accessToken?: string) {
  return {
    get: (name: string) =>
      name === "friday_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

function getRequest(url: string) {
  return new NextRequest(url, { method: "GET" });
}

describe("GET /api/integrations/telegram/link-status", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/integrations/telegram/link-status/route");
    const response = await GET(
      getRequest("http://localhost:3001/api/integrations/telegram/link-status?workspace=w1"),
    );

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires a workspace query parameter", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/integrations/telegram/link-status/route");
    const response = await GET(getRequest("http://localhost:3001/api/integrations/telegram/link-status"));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the link status from the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ linked: true, chat_id: "123", linked_at: "2026-07-13T00:00:00Z" }), {
            status: 200,
          }),
      ),
    );

    const { GET } = await import("@/app/api/integrations/telegram/link-status/route");
    const response = await GET(
      getRequest("http://localhost:3001/api/integrations/telegram/link-status?workspace=w1"),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      linked: true,
      chat_id: "123",
      linked_at: "2026-07-13T00:00:00Z",
    });
  });
});
