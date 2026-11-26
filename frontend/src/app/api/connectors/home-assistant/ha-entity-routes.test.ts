import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// Phase 13 D11 BFF routes (/api/connectors/home-assistant/*): workspace and
// entity ids validated before interpolation, Origin checked on mutations,
// auth before the backend.

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

const WS = "22222222-2222-4222-8222-222222222222";
const ENTITY = "33333333-3333-4333-8333-333333333333";
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

describe("Home Assistant entity BFF routes (Phase 13 D11)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.unstubAllGlobals();
  });

  it("list, create and candidates forward to the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubFetch(200, { results: [] });
    const list = await import("@/app/api/connectors/home-assistant/entities/route");
    const candidates = await import("@/app/api/connectors/home-assistant/candidates/route");
    expect((await list.GET(req(`/api/connectors/home-assistant/entities?workspace=${WS}`))).status).toBe(200);
    await list.POST(req(`/api/connectors/home-assistant/entities`, "POST", { workspace: WS }));
    await candidates.GET(req(`/api/connectors/home-assistant/candidates?workspace=${WS}`));
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method ?? "GET"])).toEqual([
      [`${BACKEND}/connectors/home-assistant/entities?workspace=${WS}`, "GET"],
      [`${BACKEND}/connectors/home-assistant/entities`, "POST"],
      [`${BACKEND}/connectors/home-assistant/candidates?workspace=${WS}`, "GET"],
    ]);
  });

  it("refuses a missing or malformed workspace before the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubFetch();
    const list = await import("@/app/api/connectors/home-assistant/entities/route");
    expect((await list.GET(req(`/api/connectors/home-assistant/entities`))).status).toBe(400);
    expect(
      (await list.GET(req(`/api/connectors/home-assistant/entities?workspace=../x`))).status,
    ).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("patch and delete validate the id and the origin", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubFetch(204);
    const route = await import("@/app/api/connectors/home-assistant/entities/[entityId]/route");
    expect(
      (await route.PATCH(req(`/api/x`, "PATCH", { enabled: false }), p({ entityId: "../x" }))).status,
    ).toBe(404);
    expect(
      (
        await route.DELETE(
          req(`/api/x`, "DELETE", undefined, { origin: "https://evil.example" }),
          p({ entityId: ENTITY }),
        )
      ).status,
    ).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await route.DELETE(req(`/api/x`, "DELETE"), p({ entityId: ENTITY }))).status).toBe(204);
    expect(fetchMock.mock.calls[0]![0]).toBe(`${BACKEND}/connectors/home-assistant/entities/${ENTITY}`);
  });

  it("refuses without a session before the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = stubFetch();
    const route = await import("@/app/api/connectors/home-assistant/entities/route");
    expect((await route.GET(req(`/api/connectors/home-assistant/entities?workspace=${WS}`))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
