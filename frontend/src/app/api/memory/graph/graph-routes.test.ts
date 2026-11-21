import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// Phase 8B branch 9 BFF routes (the graph, the Graphify runs, the eval
// runs): auth before backend, Origin/Content-Type before cookies on
// mutations, segments and action names validated before interpolation,
// query params allow-listed, the backend's status and body forwarded as-is.

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

function get(path: string) {
  return new NextRequest(`http://localhost:3001${path}`, { method: "GET" });
}

function mutate(path: string, method: string, body?: unknown, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001${path}`, {
    method,
    headers: { origin: "http://localhost:3001", "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function stubBackend(status = 200, body: unknown = { ok: true }) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async () =>
    status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(body), { status }),
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
    ["@/app/api/memory/graph/entities/route", `/api/memory/graph/entities?workspace=${WS}&q=api&kind=service&evil=1`, {}, `/memory/graph/entities?workspace=${WS}&q=api&kind=service`],
    ["@/app/api/memory/graph/entities/[entityId]/route", `/x?hops=2&as_of=2026-03-01T23:59:59`, { entityId: ID }, `/memory/graph/entities/${ID}?hops=2&as_of=2026-03-01T23%3A59%3A59`],
    ["@/app/api/memory/graph/entities/[entityId]/facts/route", `/x?as_of=2026-03-01T23:59:59`, { entityId: ID }, `/memory/graph/entities/${ID}/facts?as_of=2026-03-01T23%3A59%3A59`],
    ["@/app/api/memory/graph/facts/route", `/x?workspace=${WS}&current=true`, {}, `/memory/graph/facts?workspace=${WS}&current=true`],
    ["@/app/api/memory/graph/facts/[factId]/route", "/x", { factId: ID }, `/memory/graph/facts/${ID}`],
    ["@/app/api/memory/graph/graphify-runs/route", `/x?workspace=${WS}`, {}, `/harness/graphify-runs?workspace=${WS}`],
    ["@/app/api/memory/evals/runs/route", "/api/memory/evals/runs?case=1", {}, "/memory/evals/runs"],
  ];

  it.each(cases)("%s: 401 without a cookie, forwards with the token", async (mod, path, params, backend) => {
    const { GET } = (await import(mod)) as { GET: Handler };
    cookiesMock.mockResolvedValue(cookieStore());
    let fetchMock = stubBackend();
    expect((await GET(get(path), p(params))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();

    cookiesMock.mockResolvedValue(cookieStore("token"));
    fetchMock = stubBackend(200, [{ id: "x" }]);
    const response = await GET(get(path), p(params));
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url.endsWith(backend)).toBe(true);
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
    expect(response.status).toBe(200);
  });

  it("requires a UUID workspace and valid segments before the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend();
    const entities = await import("@/app/api/memory/graph/entities/route");
    expect((await entities.GET(get("/x"))).status).toBe(400);
    expect((await entities.GET(get("/x?workspace=nope"))).status).toBe(400);
    const runs = await import("@/app/api/memory/graph/graphify-runs/route");
    expect((await runs.GET(get("/x?workspace=nope"))).status).toBe(400);
    const entity = await import("@/app/api/memory/graph/entities/[entityId]/route");
    expect((await entity.GET(get("/x"), p({ entityId: "../ai" }))).status).toBe(404);
    const fact = await import("@/app/api/memory/graph/facts/[factId]/route");
    expect((await fact.GET(get("/x"), p({ factId: "nope" }))).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards a backend 403 as-is", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    stubBackend(403, { code: "owner_required" });
    const { GET } = await import("@/app/api/memory/evals/runs/route");
    const response = await GET(get("/x"));
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ code: "owner_required" });
  });
});

describe("mutation routes", () => {
  const cases: [string, string, unknown, Record<string, string>, string][] = [
    ["@/app/api/memory/graph/facts/route", "POST", { workspace: WS, subject_kind: "service", subject_name: "web", predicate: "uses", object_text: "react" }, {}, "/memory/graph/facts"],
    ["@/app/api/memory/graph/facts/[factId]/route", "PATCH", { statement: "yeni" }, { factId: ID }, `/memory/graph/facts/${ID}`],
    ["@/app/api/memory/graph/facts/[factId]/[action]/route", "POST", { valid_from: "2026-03-01T00:00:00", object_text: "aws" }, { factId: ID, action: "supersede" }, `/memory/graph/facts/${ID}/supersede`],
  ];

  it.each(cases)("%s %s: origin, content type, auth, then forwards", async (mod, method, body, params, backend) => {
    const handler = (await import(mod))[method] as Handler;
    cookiesMock.mockResolvedValue(cookieStore("token"));
    let fetchMock = stubBackend();
    expect((await handler(mutate("/x", method, body, { origin: "https://evil.example" }), p(params))).status).toBe(403);
    expect((await handler(mutate("/x", method, body, { "content-type": "text/plain" }), p(params))).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();

    cookiesMock.mockResolvedValue(cookieStore());
    expect((await handler(mutate("/x", method, body), p(params))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();

    cookiesMock.mockResolvedValue(cookieStore("token"));
    fetchMock = stubBackend(200, { id: "r1" });
    const response = await handler(mutate("/x", method, body), p(params));
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url.endsWith(backend)).toBe(true);
    expect(init?.method).toBe(method);
    expect(JSON.parse(init?.body as string)).toEqual(body);
    expect(response.status).toBe(200);
  });

  it("fact actions: only the four, a body for supersede only", async () => {
    const { POST } = await import("@/app/api/memory/graph/facts/[factId]/[action]/route");
    cookiesMock.mockResolvedValue(cookieStore("token"));
    let fetchMock = stubBackend();
    for (const params of [
      { factId: ID, action: "purge" },
      { factId: ID, action: "toString" },
      { factId: "nope", action: "approve" },
    ]) {
      expect((await POST(mutate("/x", "POST", {}), p(params))).status).toBe(404);
    }
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock = stubBackend(200, { id: ID });
    for (const action of ["approve", "reject", "restore"]) {
      await POST(mutate("/x", "POST"), p({ factId: ID, action }));
    }
    expect(fetchMock.mock.calls.map(([url]) => url.split("/").pop())).toEqual(["approve", "reject", "restore"]);
    expect(fetchMock.mock.calls.every(([, init]) => init?.body === undefined)).toBe(true);
  });

  it("entity restore sends no body; deletes check origin and segments", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    let fetchMock = stubBackend(200, { facts: 2 });
    const restore = await import("@/app/api/memory/graph/entities/[entityId]/restore/route");
    await restore.POST(mutate("/x", "POST"), p({ entityId: ID }));
    expect(fetchMock.mock.calls[0]![0].endsWith(`/memory/graph/entities/${ID}/restore`)).toBe(true);
    expect(fetchMock.mock.calls[0]![1]?.body).toBeUndefined();

    const entity = await import("@/app/api/memory/graph/entities/[entityId]/route");
    const fact = await import("@/app/api/memory/graph/facts/[factId]/route");
    fetchMock = stubBackend(204);
    expect((await entity.DELETE(mutate("/x", "DELETE", undefined, { origin: "https://evil.example" }), p({ entityId: ID }))).status).toBe(403);
    expect((await fact.DELETE(mutate("/x", "DELETE"), p({ factId: "nope" }))).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock = stubBackend(200, { operation_id: "op", facts: 3 });
    const response = await entity.DELETE(mutate("/x", "DELETE"), p({ entityId: ID }));
    expect(response.status).toBe(200);
    expect(fetchMock.mock.calls[0]![1]?.method).toBe("DELETE");
    fetchMock = stubBackend(204);
    expect((await fact.DELETE(mutate("/x", "DELETE"), p({ factId: ID }))).status).toBe(204);
  });
});
