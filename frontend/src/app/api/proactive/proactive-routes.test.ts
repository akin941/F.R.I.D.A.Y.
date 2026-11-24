import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// Phase 12 branch 2 BFF routes (/api/proactive/*): auth before backend,
// Origin/Content-Type before cookies on mutations, segments, kinds and the
// workspace validated before interpolation, the backend's status and body
// forwarded as-is.

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

describe("GET routes", () => {
  const cases: [string, string, Record<string, string>, string][] = [
    ["@/app/api/proactive/schedules/route", `/api/proactive/schedules?workspace=${WS}`, {}, `/proactive/schedules?workspace=${WS}`],
    ["@/app/api/proactive/notices/route", `/api/proactive/notices?workspace=${WS}&state=all`, {}, `/proactive/notices?workspace=${WS}&state=all`],
    ["@/app/api/proactive/pause/route", `/api/proactive/pause?workspace=${WS}`, {}, `/proactive/pause?workspace=${WS}`],
    ["@/app/api/proactive/digests/latest/route", `/api/proactive/digests/latest?workspace=${WS}`, {}, `/proactive/digests/latest?workspace=${WS}`],
    ["@/app/api/proactive/digests/[digestId]/route", `/api/proactive/digests/${ID}`, { digestId: ID }, `/proactive/digests/${ID}`],
  ];

  it.each(cases)("%s: 401 without a cookie, forwards with the token", async (mod, path, params, backend) => {
    const { GET } = (await import(mod)) as { GET: Handler };
    cookiesMock.mockResolvedValue(cookieStore());
    let fetchMock = stubBackend();
    expect((await GET(get(path), p(params))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();

    cookiesMock.mockResolvedValue(cookieStore("token"));
    fetchMock = stubBackend(200, { digest: null });
    const response = await GET(get(path), p(params));
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url.endsWith(backend)).toBe(true);
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
    expect(response.status).toBe(200);
  });

  it("refuses a bad workspace or digest id before the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend();
    const { GET: latest } = (await import("@/app/api/proactive/digests/latest/route")) as { GET: Handler };
    expect((await latest(get("/api/proactive/digests/latest?workspace=nope"), p({}))).status).toBe(400);
    const { GET: detail } = (await import("@/app/api/proactive/digests/[digestId]/route")) as { GET: Handler };
    expect((await detail(get("/api/proactive/digests/x"), p({ digestId: "../x" }))).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("mutations", () => {
  const cases: [string, string, string, Record<string, string>, unknown, string][] = [
    ["@/app/api/proactive/schedules/[kind]/route", "PUT", `/api/proactive/schedules/heartbeat?workspace=${WS}`, { kind: "heartbeat" }, { enabled: true }, `/proactive/schedules/heartbeat?workspace=${WS}`],
    ["@/app/api/proactive/notices/[noticeId]/read/route", "POST", `/api/proactive/notices/${ID}/read`, { noticeId: ID }, undefined, `/proactive/notices/${ID}/read`],
    ["@/app/api/proactive/notices/dismiss-all/route", "POST", `/api/proactive/notices/dismiss-all?workspace=${WS}`, {}, undefined, `/proactive/notices/dismiss-all?workspace=${WS}`],
    ["@/app/api/proactive/pause/route", "POST", `/api/proactive/pause?workspace=${WS}`, {}, undefined, `/proactive/pause?workspace=${WS}`],
    ["@/app/api/proactive/pause/route", "DELETE", `/api/proactive/pause?workspace=${WS}`, {}, undefined, `/proactive/pause?workspace=${WS}`],
    ["@/app/api/proactive/digests/[digestId]/ack/route", "POST", `/api/proactive/digests/${ID}/ack`, { digestId: ID }, { event: "opened" }, `/proactive/digests/${ID}/ack`],
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

  it("refuses an unknown schedule kind and a missing workspace", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend();
    const { PUT } = (await import("@/app/api/proactive/schedules/[kind]/route")) as { PUT: Handler };
    expect((await PUT(mutate(`/api/proactive/schedules/cron?workspace=${WS}`, "PUT", {}), p({ kind: "cron" }))).status).toBe(404);
    expect((await PUT(mutate("/api/proactive/schedules/heartbeat", "PUT", {}), p({ kind: "heartbeat" }))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
