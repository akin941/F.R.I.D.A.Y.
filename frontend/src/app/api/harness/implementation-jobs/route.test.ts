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
  return new NextRequest("http://localhost:3001/api/harness/implementation-jobs", {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

describe("GET /api/harness/implementation-jobs", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/harness/implementation-jobs/route");
    const response = await GET(getRequest("http://localhost:3001/api/harness/implementation-jobs?workspace=w1"));

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires a workspace query parameter", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/harness/implementation-jobs/route");
    const response = await GET(getRequest("http://localhost:3001/api/harness/implementation-jobs"));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the workspace filter to the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify([]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/harness/implementation-jobs/route");
    await GET(getRequest("http://localhost:3001/api/harness/implementation-jobs?workspace=w1"));

    const calledUrl = fetchMock.mock.calls[0]?.[0] as string;
    expect(calledUrl).toContain("/harness/implementation-jobs?");
    expect(calledUrl).toContain("workspace=w1");
  });

  it("encodes the workspace value so it cannot inject extra query parameters", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify([]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/harness/implementation-jobs/route");
    await GET(
      getRequest(
        "http://localhost:3001/api/harness/implementation-jobs?workspace=w1%26status%3Dx",
      ),
    );

    const calledUrl = fetchMock.mock.calls[0]?.[0] as string;
    expect(calledUrl).toContain("workspace=w1%26status%3Dx");
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

    const { GET } = await import("@/app/api/harness/implementation-jobs/route");
    const response = await GET(
      getRequest("http://localhost:3001/api/harness/implementation-jobs?workspace=w1"),
    );

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ detail: "Role does not allow this." });
  });
});

describe("POST /api/harness/implementation-jobs", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/harness/implementation-jobs/route");
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

    const { POST } = await import("@/app/api/harness/implementation-jobs/route");
    const response = await POST(
      postRequest({ workspace: "w1" }, { "content-type": "text/plain" }),
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/harness/implementation-jobs/route");
    const response = await POST(postRequest({ workspace: "w1" }));

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the request body (incl. discovery_job) and the created job back", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const created = { id: "j1", goal: "fix the bug", status: "pending_gate_a" };
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify(created), { status: 201 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/harness/implementation-jobs/route");
    const payload = { workspace: "w1", repo: "r1", goal: "fix the bug", discovery_job: "d1" };
    const response = await POST(postRequest(payload));

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual(created);
    const [calledUrl, init] = fetchMock.mock.calls[0] ?? [];
    expect(calledUrl).toMatch(/\/harness\/implementation-jobs$/);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual(payload);
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
  });

  it("forwards a 400 service error (e.g. repo disabled) unchanged", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "Repo is disabled." }), { status: 400 }),
      ),
    );

    const { POST } = await import("@/app/api/harness/implementation-jobs/route");
    const response = await POST(postRequest({ workspace: "w1", repo: "r1", goal: "x" }));

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "Repo is disabled." });
  });

  it("rejects an unparseable JSON body with 400 without calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/harness/implementation-jobs/route");
    const request = new NextRequest("http://localhost:3001/api/harness/implementation-jobs", {
      method: "POST",
      headers: { origin: "http://localhost:3001", "content-type": "application/json" },
      body: "{not json",
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
