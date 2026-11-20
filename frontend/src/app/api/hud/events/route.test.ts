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

const WS = "11111111-1111-4111-8111-111111111111";

function get(query: string) {
  return new NextRequest(`http://localhost:3001/api/hud/events${query}`, { method: "GET" });
}

describe("GET /api/hud/events (BFF)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.unstubAllGlobals();
  });

  it("401 without an access cookie, no backend call", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("@/app/api/hud/events/route");
    const response = await GET(get(`?workspace=${WS}`));
    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([[""], ["?workspace=nope"], [`?workspace=${WS}&date=tomorrow`]])(
    "rejects a malformed query %s before calling the backend",
    async (query) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);
      const { GET } = await import("@/app/api/hud/events/route");
      const response = await GET(get(query));
      expect(response.status).toBe(400);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("streams the backend body through with the bearer token, forwarding only known params", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"type":"end","reason":"max_age"}\n\n'));
        controller.close();
      },
    });
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("@/app/api/hud/events/route");
    const response = await GET(get(`?workspace=${WS}&date=2026-11-20&evil=1`));
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("text/event-stream");
    expect(response.headers.get("cache-control")).toBe("no-cache, no-store");
    expect(await response.text()).toContain('"type":"end"');
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(`http://127.0.0.1:8001/api/v1/hud/events?workspace=${WS}&date=2026-11-20`);
    expect(init?.method).toBe("GET");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
    expect((init?.headers as Record<string, string>).Accept).toBe("text/event-stream");
  });

  it("passes backend refusals through as JSON (403/404/429 with Retry-After)", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ code: "throttled" }), { status: 429, headers: { "Retry-After": "30" } }),
      ),
    );
    const { GET } = await import("@/app/api/hud/events/route");
    const response = await GET(get(`?workspace=${WS}`));
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("30");
    expect((await response.json()).code).toBe("throttled");
  });

  it("502 when the backend is unreachable", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("ECONNREFUSED"))));
    const { GET } = await import("@/app/api/hud/events/route");
    expect((await GET(get(`?workspace=${WS}`))).status).toBe(502);
  });

  it("is read-only: exports GET only", async () => {
    const route = await import("@/app/api/hud/events/route");
    expect(Object.keys(route).filter((k) => /^[A-Z]+$/.test(k))).toEqual(["GET"]);
  });
});
