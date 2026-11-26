import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// Phase 13 D2 BFF routes (/api/workspaces/[id]/rooms*): ids validated
// before interpolation, Origin checked on mutations, auth before backend.

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

const WS = "22222222-2222-4222-8222-222222222222";
const ROOM = "33333333-3333-4333-8333-333333333333";
const BACKEND = "http://127.0.0.1:8001/api/v1";

function cookieStore(accessToken?: string) {
  return {
    get: (name: string) =>
      name === "friday_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

function req(path: string, method = "GET", body?: unknown, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001${path}`, {
    method,
    headers: { origin: "http://localhost:3001", "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function stubFetch(status = 200, payload: unknown = {}) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
    async () => new Response(status === 204 ? null : JSON.stringify(payload), { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const p = <T extends Record<string, string>>(value: T) => ({ params: Promise.resolve(value) });

describe("room BFF routes (Phase 13 D2)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.unstubAllGlobals();
  });

  it("list and create forward to the workspace's rooms", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubFetch(200, { results: [] });
    const route = await import("@/app/api/workspaces/[workspaceId]/rooms/route");
    expect((await route.GET(req(`/api/workspaces/${WS}/rooms`), p({ workspaceId: WS }))).status).toBe(200);
    await route.POST(req(`/api/workspaces/${WS}/rooms`, "POST", { name: "Salon" }), p({ workspaceId: WS }));
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method ?? "GET"])).toEqual([
      [`${BACKEND}/workspaces/${WS}/rooms`, "GET"],
      [`${BACKEND}/workspaces/${WS}/rooms`, "POST"],
    ]);
  });

  it("patch and delete validate both ids and the origin", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubFetch(204);
    const route = await import("@/app/api/workspaces/[workspaceId]/rooms/[roomId]/route");
    expect(
      (await route.PATCH(req(`/api/x`, "PATCH", { name: "A" }), p({ workspaceId: WS, roomId: "../x" }))).status,
    ).toBe(404);
    expect(
      (
        await route.DELETE(
          req(`/api/x`, "DELETE", undefined, { origin: "https://evil.example" }),
          p({ workspaceId: WS, roomId: ROOM }),
        )
      ).status,
    ).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(
      (await route.DELETE(req(`/api/x`, "DELETE"), p({ workspaceId: WS, roomId: ROOM }))).status,
    ).toBe(204);
    expect(fetchMock.mock.calls[0]![0]).toBe(`${BACKEND}/workspaces/${WS}/rooms/${ROOM}`);
  });

  it("refuses without a session before the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = stubFetch();
    const route = await import("@/app/api/workspaces/[workspaceId]/rooms/route");
    expect((await route.GET(req(`/api/workspaces/${WS}/rooms`), p({ workspaceId: WS }))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
