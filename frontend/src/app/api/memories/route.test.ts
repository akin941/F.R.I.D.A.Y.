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

function postRequest(body: unknown, headers: Record<string, string> = {}) {
  return new NextRequest("http://localhost:3001/api/memories", {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

describe("GET /api/memories", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/memories/route");
    const response = await GET(getRequest("http://localhost:3001/api/memories?workspace=w1"));

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires a workspace query parameter", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/memories/route");
    const response = await GET(getRequest("http://localhost:3001/api/memories"));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards status and type filters to the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify([]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/memories/route");
    await GET(
      getRequest(
        "http://localhost:3001/api/memories?workspace=w1&status=approved&type=semantic",
      ),
    );

    const calledUrl = fetchMock.mock.calls[0]?.[0] as string;
    expect(calledUrl).toContain("workspace=w1");
    expect(calledUrl).toContain("status=approved");
    expect(calledUrl).toContain("type=semantic");
  });
});

describe("POST /api/memories", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/memories/route");
    const response = await POST(
      postRequest({ workspace: "w1" }, { origin: "https://evil.example" }),
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/memories/route");
    const response = await POST(postRequest({ workspace: "w1" }, { "content-type": "text/plain" }));

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/memories/route");
    const response = await POST(postRequest({ workspace: "w1" }));

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the created memory body and status from the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ id: "m1", title: "Coffee" }), { status: 201 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/memories/route");
    const response = await POST(
      postRequest({
        workspace: "w1",
        title: "Coffee",
        content: "Drinks coffee black.",
        memory_type: "preference",
        sensitivity: "normal",
      }),
    );

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ id: "m1", title: "Coffee" });
  });
});
