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

describe("GET /api/knowledge/documents", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/knowledge/documents/route");
    const response = await GET(
      getRequest("http://localhost:3001/api/knowledge/documents?workspace=w1"),
    );

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires a workspace query parameter", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/knowledge/documents/route");
    const response = await GET(getRequest("http://localhost:3001/api/knowledge/documents"));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the status filter to the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify([]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/knowledge/documents/route");
    await GET(
      getRequest("http://localhost:3001/api/knowledge/documents?workspace=w1&status=ready"),
    );

    const calledUrl = fetchMock.mock.calls[0]?.[0] as string;
    expect(calledUrl).toContain("workspace=w1");
    expect(calledUrl).toContain("status=ready");
  });

  it("forwards the document list body and status from the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify([{ id: "d1", title: "Doc" }]), { status: 200 })),
    );

    const { GET } = await import("@/app/api/knowledge/documents/route");
    const response = await GET(
      getRequest("http://localhost:3001/api/knowledge/documents?workspace=w1"),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual([{ id: "d1", title: "Doc" }]);
  });
});
