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
  return new NextRequest("http://localhost:3001/api/approvals/requests", {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

describe("GET /api/approvals/requests", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/approvals/requests/route");
    const response = await GET(getRequest("http://localhost:3001/api/approvals/requests?workspace=w1"));

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires a workspace query parameter", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/approvals/requests/route");
    const response = await GET(getRequest("http://localhost:3001/api/approvals/requests"));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the status filter to the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify([]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/approvals/requests/route");
    await GET(
      getRequest("http://localhost:3001/api/approvals/requests?workspace=w1&status=pending"),
    );

    const calledUrl = fetchMock.mock.calls[0]?.[0] as string;
    expect(calledUrl).toContain("workspace=w1");
    expect(calledUrl).toContain("status=pending");
  });

  it("forwards scope=all without a workspace, plus every inbox filter", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify([]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/approvals/requests/route");
    const response = await GET(
      getRequest(
        "http://localhost:3001/api/approvals/requests?scope=all&status=pending&tool=harness_push&origin=harness&requested_channel=system&payload=x",
      ),
    );

    expect(response.status).toBe(200);
    const calledUrl = new URL(fetchMock.mock.calls[0]?.[0] as string);
    expect(Object.fromEntries(calledUrl.searchParams)).toEqual({
      scope: "all",
      status: "pending",
      tool: "harness_push",
      origin: "harness",
      requested_channel: "system",
    });
  });

  it("forwards an optional workspace narrowing with scope=all", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify([]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/approvals/requests/route");
    await GET(getRequest("http://localhost:3001/api/approvals/requests?scope=all&workspace=w2"));

    const calledUrl = new URL(fetchMock.mock.calls[0]?.[0] as string);
    expect(calledUrl.searchParams.get("scope")).toBe("all");
    expect(calledUrl.searchParams.get("workspace")).toBe("w2");
  });
});

describe("POST /api/approvals/requests", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/approvals/requests/route");
    const response = await POST(
      postRequest({ workspace: "w1", tool_name: "vault_export" }, { origin: "https://evil.example" }),
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/approvals/requests/route");
    const response = await POST(
      postRequest({ workspace: "w1", tool_name: "vault_export" }, { "content-type": "text/plain" }),
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/approvals/requests/route");
    const response = await POST(postRequest({ workspace: "w1", tool_name: "vault_export" }));

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the created request body and status from the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ id: "r1", tool_name: "vault_export", status: "pending" }), {
          status: 201,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/approvals/requests/route");
    const response = await POST(postRequest({ workspace: "w1", tool_name: "vault_export" }));

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ id: "r1", tool_name: "vault_export", status: "pending" });
  });
});
