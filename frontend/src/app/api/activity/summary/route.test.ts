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

describe("GET /api/activity/summary", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns a coded 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/activity/summary/route");
    const response = await GET(getRequest("http://localhost:3001/api/activity/summary"));

    expect(response.status).toBe(401);
    expect((await response.json()).code).toBe("not_authenticated");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("proxies to the backend with the bearer token and passes the body through", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const body = {
      workspaces: [{ workspace: "w1", pending_approvals: 2, live_harness_jobs: 0, live_agents: 1 }],
      totals: { pending_approvals: 2, live_harness_jobs: 0, live_agents: 1 },
    };
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify(body), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/activity/summary/route");
    const response = await GET(getRequest("http://localhost:3001/api/activity/summary"));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(body);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://127.0.0.1:8001/api/v1/activity/summary");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
  });

  it("forwards an optional workspace filter and the backend's error status", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify({ code: "not_found" }), { status: 404 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/activity/summary/route");
    const response = await GET(
      getRequest("http://localhost:3001/api/activity/summary?workspace=w9"),
    );

    expect(response.status).toBe(404);
    expect(fetchMock.mock.calls[0]![0]).toBe(
      "http://127.0.0.1:8001/api/v1/activity/summary?workspace=w9",
    );
  });
});
