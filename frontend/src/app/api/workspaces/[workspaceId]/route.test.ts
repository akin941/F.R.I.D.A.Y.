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
      name === "friday_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

function params(workspaceId: string = VALID_ID) {
  return Promise.resolve({ workspaceId });
}

function jsonRequest(method: string, body?: unknown, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/workspaces/${VALID_ID}`, {
    method,
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

// Codex finding precedent (memory/knowledge/conversations/tasks routes): an
// unvalidated dynamic segment interpolated into a server-side,
// bearer-authenticated fetch could redirect the upstream request to an
// unintended backend path. Every handler must reject a non-UUID workspaceId
// with 404 before it ever reaches getAccessToken()/backendFetch.
const INJECTION_IDS = ["../ai/conversations", "not-a-uuid", "11111111-1111-1111-1111-11111111111g"];

describe("GET /api/workspaces/[workspaceId]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/workspaces/[workspaceId]/route");
    const response = await GET(jsonRequest("GET"), { params: params() });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards a 404 from the backend for an out-of-scope workspace", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 404 })),
    );

    const { GET } = await import("@/app/api/workspaces/[workspaceId]/route");
    const response = await GET(jsonRequest("GET"), { params: params() });

    expect(response.status).toBe(404);
  });

  it("forwards the workspace detail (including profile) on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const detail = {
      id: VALID_ID,
      name: "Acme",
      type: "personal",
      enabled_modules: [],
      profile: { repos: [], services: [], deploy_targets: [], notes: "" },
      role: "owner",
      created_at: "2026-01-01T00:00:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(detail), { status: 200 })),
    );

    const { GET } = await import("@/app/api/workspaces/[workspaceId]/route");
    const response = await GET(jsonRequest("GET"), { params: params() });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(detail);
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID workspaceId (%s) with 404 before touching cookies or the backend",
    async (workspaceId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { GET } = await import("@/app/api/workspaces/[workspaceId]/route");
      const response = await GET(jsonRequest("GET"), { params: params(workspaceId) });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );
});

describe("PATCH /api/workspaces/[workspaceId]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before calling the backend or reading cookies", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { PATCH } = await import("@/app/api/workspaces/[workspaceId]/route");
    const response = await PATCH(
      jsonRequest("PATCH", { profile: {} }, { origin: "https://evil.example" }),
      { params: params() },
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before calling the backend or reading cookies", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { PATCH } = await import("@/app/api/workspaces/[workspaceId]/route");
    const response = await PATCH(
      jsonRequest("PATCH", { profile: {} }, { "content-type": "text/plain" }),
      { params: params() },
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { PATCH } = await import("@/app/api/workspaces/[workspaceId]/route");
    const response = await PATCH(jsonRequest("PATCH", { profile: {} }), { params: params() });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns 400 for an invalid JSON body without calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const request = new NextRequest(`http://localhost:3001/api/workspaces/${VALID_ID}`, {
      method: "PATCH",
      headers: { origin: "http://localhost:3001", "content-type": "application/json" },
      body: "not json",
    });

    const { PATCH } = await import("@/app/api/workspaces/[workspaceId]/route");
    const response = await PATCH(request, { params: params() });

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the updated workspace on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const updated = {
      id: VALID_ID,
      name: "Acme",
      type: "personal",
      enabled_modules: [],
      profile: { notes: "updated" },
      role: "owner",
      created_at: "2026-01-01T00:00:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(updated), { status: 200 })),
    );

    const { PATCH } = await import("@/app/api/workspaces/[workspaceId]/route");
    const response = await PATCH(jsonRequest("PATCH", { profile: { notes: "updated" } }), {
      params: params(),
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(updated);
  });

  it("forwards a 403 from the backend (non-owner PATCH)", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "Forbidden." }), { status: 403 })),
    );

    const { PATCH } = await import("@/app/api/workspaces/[workspaceId]/route");
    const response = await PATCH(jsonRequest("PATCH", { profile: {} }), { params: params() });

    expect(response.status).toBe(403);
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID workspaceId (%s) with 404 before touching cookies or the backend",
    async (workspaceId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { PATCH } = await import("@/app/api/workspaces/[workspaceId]/route");
      const response = await PATCH(jsonRequest("PATCH", { profile: {} }), {
        params: params(workspaceId),
      });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );
});
