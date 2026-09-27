import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

const VALID_ID = "11111111-1111-1111-1111-111111111111";
const INJECTION_IDS = ["../ai/conversations", "not-a-uuid"];

function cookieStore(accessToken?: string) {
  return {
    get: (name: string) =>
      name === "jarvis_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

function params(repoId: string = VALID_ID) {
  return Promise.resolve({ repoId });
}

function jsonRequest(method: string, body?: unknown, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/harness/repos/${VALID_ID}`, {
    method,
    headers: { origin: "http://localhost:3001", "content-type": "application/json", ...headers },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

describe("/api/harness/repos/[repoId]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET, PATCH, DELETE } = await import("@/app/api/harness/repos/[repoId]/route");
    expect((await GET(jsonRequest("GET"), { params: params() })).status).toBe(401);
    expect((await PATCH(jsonRequest("PATCH", {}), { params: params() })).status).toBe(401);
    expect((await DELETE(jsonRequest("DELETE"), { params: params() })).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(INJECTION_IDS)("rejects a non-UUID repoId (%s) before the backend", async (repoId) => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET, PATCH, DELETE } = await import("@/app/api/harness/repos/[repoId]/route");
    expect((await GET(jsonRequest("GET"), { params: params(repoId) })).status).toBe(404);
    expect((await PATCH(jsonRequest("PATCH", {}), { params: params(repoId) })).status).toBe(404);
    expect((await DELETE(jsonRequest("DELETE"), { params: params(repoId) })).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a cross-origin PATCH/DELETE before the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { PATCH, DELETE } = await import("@/app/api/harness/repos/[repoId]/route");
    const evil = { origin: "https://evil.example" };
    expect((await PATCH(jsonRequest("PATCH", {}, evil), { params: params() })).status).toBe(403);
    expect((await DELETE(jsonRequest("DELETE", undefined, evil), { params: params() })).status).toBe(
      403,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards a PATCH body with the bearer token", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify({ id: VALID_ID, enabled: true }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { PATCH } = await import("@/app/api/harness/repos/[repoId]/route");
    const response = await PATCH(jsonRequest("PATCH", { enabled: true }), { params: params() });

    const [calledUrl, init] = fetchMock.mock.calls[0] ?? [];
    expect(calledUrl).toContain(`/harness/repos/${VALID_ID}`);
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(init?.body as string)).toEqual({ enabled: true });
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
    expect(response.status).toBe(200);
  });

  it("maps a backend 204 delete to an empty 204 and forwards a 400", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    const { DELETE } = await import("@/app/api/harness/repos/[repoId]/route");
    const ok = await DELETE(jsonRequest("DELETE"), { params: params() });
    expect(ok.status).toBe(204);
    expect(await ok.text()).toBe("");

    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ code: "harness.invalid_transition" }), { status: 400 }),
      ),
    );
    const refused = await DELETE(jsonRequest("DELETE"), { params: params() });
    expect(refused.status).toBe(400);
    expect(await refused.json()).toEqual({ code: "harness.invalid_transition" });
  });
});
