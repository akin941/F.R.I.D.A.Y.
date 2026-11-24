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

const DEVICE = "44444444-4444-4444-8444-444444444444";
const BACKEND = "http://127.0.0.1:8001/api/v1";

function stubFetch(status = 200, payload: unknown = {}) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
    async () => new Response(JSON.stringify(payload), { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function del(path: string, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001${path}`, {
    method: "DELETE",
    headers: { origin: "http://localhost:3001", "content-type": "application/json", ...headers },
  });
}

const params = (deviceId: string) => ({ params: Promise.resolve({ deviceId }) });

describe("live voice BFF routes (Phase 11 D12/D15)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.unstubAllGlobals();
  });

  it("GET 401 without a session, before the backend", async () => {
    cookiesMock.mockReturnValue(cookieStore());
    const fetchMock = stubFetch();
    const { GET } = await import("@/app/api/voice/live/route");
    expect((await GET()).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("GET forwards to voice-live and passes the body and status through", async () => {
    cookiesMock.mockReturnValue(cookieStore("tok"));
    const body = { always_on_enabled: false, nodes: [], models: [] };
    const fetchMock = stubFetch(200, body);
    const { GET } = await import("@/app/api/voice/live/route");
    const response = await GET();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual(body);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(`${BACKEND}/auth/nodes/voice-live`);
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });

  it("GET passes a 403 through", async () => {
    cookiesMock.mockReturnValue(cookieStore("tok"));
    stubFetch(403, { code: "owner_required" });
    const { GET } = await import("@/app/api/voice/live/route");
    expect((await GET()).status).toBe(403);
  });

  it("DELETE releases the mute of a valid device id", async () => {
    cookiesMock.mockReturnValue(cookieStore("tok"));
    const fetchMock = stubFetch(200, { device_id: DEVICE, released: true });
    const { DELETE } = await import("@/app/api/voice/live/[deviceId]/mute/route");
    const response = await DELETE(del(`/api/voice/live/${DEVICE}/mute`), params(DEVICE));
    expect(response.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(`${BACKEND}/auth/nodes/${DEVICE}/voice-mute`);
    expect(init?.method).toBe("DELETE");
  });

  it("DELETE refuses a non-UUID id, a cross-origin call and no session, before the backend", async () => {
    const fetchMock = stubFetch();
    const { DELETE } = await import("@/app/api/voice/live/[deviceId]/mute/route");
    cookiesMock.mockReturnValue(cookieStore("tok"));
    expect((await DELETE(del("/api/voice/live/x/mute"), params("../x"))).status).toBe(404);
    const crossOrigin = del(`/api/voice/live/${DEVICE}/mute`, { origin: "https://evil.test" });
    expect((await DELETE(crossOrigin, params(DEVICE))).status).toBe(403);
    cookiesMock.mockReturnValue(cookieStore());
    expect((await DELETE(del(`/api/voice/live/${DEVICE}/mute`), params(DEVICE))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
