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

const URL_BASE = "http://localhost:3001/api/tasks/trash";

describe("GET /api/tasks/trash", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without an access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/tasks/trash/route");
    const response = await GET(new NextRequest(`${URL_BASE}?workspace=w1`));

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires a workspace", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/tasks/trash/route");
    const response = await GET(new NextRequest(URL_BASE));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the (encoded) workspace with the bearer token", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify([{ id: "x" }]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/tasks/trash/route");
    const response = await GET(new NextRequest(`${URL_BASE}?workspace=w1%26x%3D1`));

    const [calledUrl, init] = fetchMock.mock.calls[0] ?? [];
    expect(calledUrl).toContain("/tasks/trash?workspace=w1%26x%3D1");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
    expect(await response.json()).toEqual([{ id: "x" }]);
  });
});
