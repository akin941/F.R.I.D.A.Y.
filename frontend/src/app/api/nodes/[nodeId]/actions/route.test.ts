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
const EXEC = "44444444-4444-4444-8444-444444444444";

function post(body: unknown, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/nodes/${NODE}/actions`, {
    method: "POST",
    headers: { origin: "http://localhost:3001", "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

describe("POST /api/nodes/[nodeId]/actions (BFF, Phase 10 D8)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.unstubAllGlobals();
  });

  it("forwards only capability, args and source_message_id", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify({ id: EXEC, status: "sent" }), { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/nodes/[nodeId]/actions/route");
    const response = await POST(
      post({ capability: "apps.open", args: { bundle_id: "com.apple.Terminal" }, source_message_id: "m", grant: "x" }),
      { params: Promise.resolve({ nodeId: NODE }) },
    );
    expect(response.status).toBe(202);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(`http://127.0.0.1:8001/api/v1/nodes/${NODE}/actions`);
    expect(JSON.parse(init!.body as string)).toEqual({
      capability: "apps.open",
      args: { bundle_id: "com.apple.Terminal" },
      source_message_id: "m",
    });
  });

  it("refuses a cross-origin request, a bad node id and a missing cookie before the backend", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { POST } = await import("@/app/api/nodes/[nodeId]/actions/route");
    cookiesMock.mockResolvedValue(cookieStore("token"));
    expect(
      (await POST(post({}, { origin: "https://evil.example" }), { params: Promise.resolve({ nodeId: NODE }) })).status,
    ).toBe(403);
    expect((await POST(post({}), { params: Promise.resolve({ nodeId: "../x" }) })).status).toBe(404);
    cookiesMock.mockResolvedValue(cookieStore());
    expect((await POST(post({}), { params: Promise.resolve({ nodeId: NODE }) })).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("GET of one execution is read-only and validates both ids", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: EXEC }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const route = await import("@/app/api/nodes/[nodeId]/actions/[executionId]/route");
    expect(Object.keys(route).filter((k) => /^[A-Z]+$/.test(k))).toEqual(["GET"]);
    const request = new NextRequest(`http://localhost:3001/api/nodes/${NODE}/actions/${EXEC}`);
    expect((await route.GET(request, { params: Promise.resolve({ nodeId: NODE, executionId: EXEC }) })).status).toBe(
      200,
    );
    expect((await route.GET(request, { params: Promise.resolve({ nodeId: NODE, executionId: "x" }) })).status).toBe(
      404,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
