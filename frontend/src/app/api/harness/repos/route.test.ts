import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

function cookieStore(accessToken?: string) {
  return {
    get: (name: string) =>
      name === "jarvis_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

function getRequest(url: string) {
  return new NextRequest(url, { method: "GET" });
}

describe("GET /api/harness/repos", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/harness/repos/route");
    const response = await GET(getRequest("http://localhost:3001/api/harness/repos?workspace=w1"));

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires a workspace query parameter", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/harness/repos/route");
    const response = await GET(getRequest("http://localhost:3001/api/harness/repos"));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the workspace filter and bearer token to the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const repos = [{ id: "r1", name: "jarvis", enabled: true }];
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify(repos), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/harness/repos/route");
    const response = await GET(
      getRequest("http://localhost:3001/api/harness/repos?workspace=w1%26x%3D1"),
    );

    const [calledUrl, init] = fetchMock.mock.calls[0] ?? [];
    expect(calledUrl).toContain("/harness/repos?");
    expect(calledUrl).toContain("workspace=w1%26x%3D1");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(repos);
  });

  it("forwards a 403 for a non-curator", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "Role does not allow this." }), { status: 403 }),
      ),
    );

    const { GET } = await import("@/app/api/harness/repos/route");
    const response = await GET(getRequest("http://localhost:3001/api/harness/repos?workspace=w1"));

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ detail: "Role does not allow this." });
  });
});
