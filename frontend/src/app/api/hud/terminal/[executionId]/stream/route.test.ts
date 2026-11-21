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

const EXEC = "44444444-4444-4444-8444-444444444444";

function get(id: string, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/hud/terminal/${id}/stream`, { method: "GET", headers });
}

const params = (executionId: string) => ({ params: Promise.resolve({ executionId }) });

describe("GET /api/hud/terminal/[executionId]/stream (BFF)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.unstubAllGlobals();
  });

  it("401 without an access cookie, no backend call", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("@/app/api/hud/terminal/[executionId]/stream/route");
    expect((await GET(get(EXEC), params(EXEC))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["nope", "../ai/conversations", "44444444-4444-4444-8444-44444444444"])(
    "404 for a malformed id %s before calling the backend",
    async (id) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);
      const { GET } = await import("@/app/api/hud/terminal/[executionId]/stream/route");
      expect((await GET(get(EXEC), params(id))).status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("streams the backend body through, forwarding only a well-formed Last-Event-ID", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () =>
        new Response('data: {"type":"end","status":"ok","exit_code":0,"truncated":false}\n\n', {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("@/app/api/hud/terminal/[executionId]/stream/route");
    const response = await GET(get(EXEC, { "Last-Event-ID": "17-0" }), params(EXEC));
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("text/event-stream");
    expect(response.headers.get("cache-control")).toBe("no-cache, no-store");
    expect(await response.text()).toContain('"status":"ok"');
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(`http://127.0.0.1:8001/api/v1/hud/terminal/${EXEC}/stream`);
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer token");
    expect(headers.Accept).toBe("text/event-stream");
    expect(headers["Last-Event-ID"]).toBe("17-0");

    await GET(get(EXEC, { "Last-Event-ID": "abc" }), params(EXEC));
    const last = fetchMock.mock.calls.at(-1)!;
    expect((last[1]?.headers as Record<string, string>)["Last-Event-ID"]).toBeUndefined();
  });

  it("passes backend refusals through as JSON", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ code: "not_found" }), { status: 404 })));
    const { GET } = await import("@/app/api/hud/terminal/[executionId]/stream/route");
    const response = await GET(get(EXEC), params(EXEC));
    expect(response.status).toBe(404);
  });

  it("502 when the backend is unreachable", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("ECONNREFUSED"))));
    const { GET } = await import("@/app/api/hud/terminal/[executionId]/stream/route");
    expect((await GET(get(EXEC), params(EXEC))).status).toBe(502);
  });

  it("is read-only: exports GET only", async () => {
    const route = await import("@/app/api/hud/terminal/[executionId]/stream/route");
    expect(Object.keys(route).filter((k) => /^[A-Z]+$/.test(k))).toEqual(["GET"]);
  });
});
