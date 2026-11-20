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

describe("GET /api/memories/export", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/memories/export/route");
    const response = await GET(
      getRequest("http://localhost:3001/api/memories/export?workspace=w1"),
    );

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires a workspace query parameter", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/memories/export/route");
    const response = await GET(getRequest("http://localhost:3001/api/memories/export"));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sets Cache-Control: no-store and forwards Content-Disposition", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ workspace: "w1", count: 0, memories: [] }), {
          status: 200,
          headers: { "Content-Disposition": 'attachment; filename="friday-memories.json"' },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/memories/export/route");
    const response = await GET(
      getRequest("http://localhost:3001/api/memories/export?workspace=w1"),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(response.headers.get("Content-Disposition")).toBe(
      'attachment; filename="friday-memories.json"',
    );
    expect(await response.json()).toEqual({ workspace: "w1", count: 0, memories: [] });
  });
});
