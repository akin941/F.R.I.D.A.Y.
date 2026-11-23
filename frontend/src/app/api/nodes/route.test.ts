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

const NODE = "22222222-2222-4222-8222-222222222222";
const BACKEND = "http://127.0.0.1:8001/api/v1";

function req(path: string, method = "GET", headers: Record<string, string> = {}, body?: unknown) {
  return new NextRequest(`http://localhost:3001${path}`, {
    method,
    headers: { origin: "http://localhost:3001", "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function stubFetch(status = 200, payload: unknown = {}) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
    async () => new Response(JSON.stringify(payload), { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const params = (nodeId: string) => ({ params: Promise.resolve({ nodeId }) });

describe("node BFF routes (Phase 9 D11)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.unstubAllGlobals();
  });

  it("GET /api/nodes, /api/nodes/[id] and invocations forward read-only", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubFetch(200, []);
    const list = await import("@/app/api/nodes/route");
    const detail = await import("@/app/api/nodes/[nodeId]/route");
    const log = await import("@/app/api/nodes/[nodeId]/invocations/route");
    for (const route of [list, detail, log]) {
      expect(Object.keys(route).filter((k) => /^[A-Z]+$/.test(k))).toEqual(["GET"]);
    }
    expect((await list.GET()).status).toBe(200);
    expect((await detail.GET(req(`/api/nodes/${NODE}`), params(NODE))).status).toBe(200);
    expect((await log.GET(req(`/api/nodes/${NODE}/invocations?cursor=58`), params(NODE))).status).toBe(200);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `${BACKEND}/nodes`,
      `${BACKEND}/nodes/${NODE}`,
      `${BACKEND}/nodes/${NODE}/invocations?cursor=58`,
    ]);
  });

  it("refuses a bad node id, a non-numeric cursor and a missing cookie before the backend", async () => {
    const fetchMock = stubFetch();
    const detail = await import("@/app/api/nodes/[nodeId]/route");
    const log = await import("@/app/api/nodes/[nodeId]/invocations/route");
    const revoke = await import("@/app/api/nodes/[nodeId]/revoke/route");
    cookiesMock.mockResolvedValue(cookieStore("token"));
    expect((await detail.GET(req("/api/nodes/x"), params("../x"))).status).toBe(404);
    expect(
      (await log.GET(req(`/api/nodes/${NODE}/invocations?cursor=1%26x=2`), params(NODE))).status,
    ).toBe(400);
    expect((await revoke.POST(req(`/api/nodes/x/revoke`, "POST"), params("x"))).status).toBe(404);
    cookiesMock.mockResolvedValue(cookieStore());
    expect((await detail.GET(req(`/api/nodes/${NODE}`), params(NODE))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("POST revoke is origin- and content-type-checked", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubFetch(200, { id: NODE, status: "revoked" });
    const revoke = await import("@/app/api/nodes/[nodeId]/revoke/route");
    expect(
      (await revoke.POST(req(`/api/nodes/${NODE}/revoke`, "POST", { origin: "https://evil.example" }), params(NODE)))
        .status,
    ).toBe(403);
    expect(
      (await revoke.POST(req(`/api/nodes/${NODE}/revoke`, "POST", { "content-type": "text/plain" }), params(NODE)))
        .status,
    ).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await revoke.POST(req(`/api/nodes/${NODE}/revoke`, "POST"), params(NODE))).status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(`${BACKEND}/nodes/${NODE}/revoke`);
    expect(init!.method).toBe("POST");
  });

  it("the kill switch proxies GET/POST/DELETE to the accounts endpoint, no body", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubFetch(200, { engaged: true });
    const route = await import("@/app/api/nodes/kill-switch/route");
    expect(Object.keys(route).filter((k) => /^[A-Z]+$/.test(k)).sort()).toEqual(["DELETE", "GET", "POST"]);
    await route.GET();
    await route.POST(req("/api/nodes/kill-switch", "POST", {}, { device_id: "*" }));
    await route.DELETE(req("/api/nodes/kill-switch", "DELETE"));
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init!.method, init!.body])).toEqual([
      [`${BACKEND}/auth/nodes/kill-switch`, "GET", undefined],
      [`${BACKEND}/auth/nodes/kill-switch`, "POST", undefined],
      [`${BACKEND}/auth/nodes/kill-switch`, "DELETE", undefined],
    ]);
    fetchMock.mockClear();
    expect(
      (await route.POST(req("/api/nodes/kill-switch", "POST", { origin: "https://evil.example" }))).status,
    ).toBe(403);
    expect(
      (await route.DELETE(req("/api/nodes/kill-switch", "DELETE", { "content-type": "text/plain" }))).status,
    ).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("node pairing codes always ask for device_kind=node and forward only kind", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubFetch(201, { code: "1", kind: "pin" });
    const route = await import("@/app/api/nodes/pairing-codes/route");
    await route.POST(req("/api/nodes/pairing-codes", "POST", {}, { kind: "strong", device_kind: "browser", x: 1 }));
    await route.POST(req("/api/nodes/pairing-codes", "POST", {}, { kind: "weird" }));
    const bodies = fetchMock.mock.calls.map(([url, init]) => {
      expect(url).toBe(`${BACKEND}/auth/pairing-codes`);
      return JSON.parse(init!.body as string);
    });
    expect(bodies).toEqual([
      { kind: "strong", device_kind: "node" },
      { kind: "pin", device_kind: "node" },
    ]);
  });
});
