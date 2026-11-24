import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// Phase 12 branch 3 BFF routes (/api/assistant/wake-routes*): auth before
// the backend, Origin before cookies on mutations, the workspace and the
// alias id validated before interpolation.

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

const ID = "11111111-1111-1111-1111-111111111111";
const WS = "22222222-2222-2222-2222-222222222222";

function cookieStore(accessToken?: string) {
  return {
    get: (name: string) =>
      name === "friday_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

const get = (path: string) => new NextRequest(`http://localhost:3001${path}`, { method: "GET" });

function mutate(path: string, method: string, body?: unknown, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001${path}`, {
    method,
    headers: { origin: "http://localhost:3001", "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function stubBackend(status = 200, body: unknown = { ok: true }) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
    async () => new Response(JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const p = <T extends Record<string, string>>(value: T) => ({ params: Promise.resolve(value) });
type Handler = (r: NextRequest, c: unknown) => Promise<Response>;

beforeEach(() => {
  vi.resetAllMocks();
});

describe("wake-route routes", () => {
  it("GET: 401 without a cookie, 400 on a bad workspace, forwards with the token", async () => {
    const { GET } = (await import("@/app/api/assistant/wake-routes/route")) as { GET: Handler };
    cookiesMock.mockResolvedValue(cookieStore());
    let fetchMock = stubBackend();
    expect((await GET(get(`/api/assistant/wake-routes?workspace=${WS}`), p({}))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();

    cookiesMock.mockResolvedValue(cookieStore("token"));
    expect((await GET(get("/api/assistant/wake-routes?workspace=nope"), p({}))).status).toBe(400);
    expect((await GET(get("/api/assistant/wake-routes"), p({}))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();

    fetchMock = stubBackend(200, { routes: [] });
    const response = await GET(get(`/api/assistant/wake-routes?workspace=${WS}`), p({}));
    expect(response.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url.endsWith(`/assistant/wake-routes?workspace=${WS}`)).toBe(true);
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
  });

  const cases: [string, string, string, Record<string, string>, unknown, string][] = [
    ["@/app/api/assistant/wake-routes/aliases/route", "POST", `/api/assistant/wake-routes/aliases?workspace=${WS}`, {}, { route: "digest.today", phrase: "kahve" }, `/assistant/wake-routes/aliases?workspace=${WS}`],
    ["@/app/api/assistant/wake-routes/aliases/[aliasId]/route", "PATCH", `/api/assistant/wake-routes/aliases/${ID}`, { aliasId: ID }, { phrase: "çay" }, `/assistant/wake-routes/aliases/${ID}`],
    ["@/app/api/assistant/wake-routes/aliases/[aliasId]/route", "DELETE", `/api/assistant/wake-routes/aliases/${ID}`, { aliasId: ID }, undefined, `/assistant/wake-routes/aliases/${ID}`],
  ];

  it.each(cases)("%s %s: cross-origin refused, then forwarded", async (mod, method, path, params, body, backend) => {
    const handler = ((await import(mod)) as Record<string, Handler>)[method]!;
    cookiesMock.mockResolvedValue(cookieStore("token"));
    let fetchMock = stubBackend();
    const foreign = await handler(mutate(path, method, body, { origin: "https://evil.example" }), p(params));
    expect(foreign.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();

    fetchMock = stubBackend(200, { ok: true });
    const response = await handler(mutate(path, method, body), p(params));
    expect(response.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url.endsWith(backend)).toBe(true);
    expect(init?.method).toBe(method);
    if (body !== undefined) expect(JSON.parse(String(init?.body))).toEqual(body);
  });

  it("refuses a bad alias id and a missing workspace before the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend();
    const detail = (await import("@/app/api/assistant/wake-routes/aliases/[aliasId]/route")) as unknown as Record<string, Handler>;
    expect((await detail.DELETE!(mutate("/api/assistant/wake-routes/aliases/x", "DELETE"), p({ aliasId: "../x" }))).status).toBe(404);
    expect((await detail.PATCH!(mutate("/api/assistant/wake-routes/aliases/x", "PATCH", { phrase: "a" }), p({ aliasId: "../x" }))).status).toBe(404);
    const { POST } = (await import("@/app/api/assistant/wake-routes/aliases/route")) as { POST: Handler };
    expect((await POST(mutate("/api/assistant/wake-routes/aliases", "POST", { route: "digest.today", phrase: "kahve" }), p({}))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
