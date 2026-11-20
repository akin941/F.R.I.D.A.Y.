import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// Phase 5 branch 9 BFF routes (connectors, deploys, harness PR panel), all
// built on lib/bff-proxy: auth before backend, Origin/Content-Type before
// cookies on mutations, path segments validated before interpolation.

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

const ID = "11111111-1111-1111-1111-111111111111";
const WS = "22222222-2222-2222-2222-222222222222";
const BAD_IDS = ["../ai/conversations", "not-a-uuid", "11111111-1111-1111-1111-11111111111g"];

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
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
    async () =>
      status === 202 ? new Response(null, { status }) : new Response(JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const p = <T extends Record<string, string>>(value: T) => ({ params: Promise.resolve(value) });

beforeEach(() => {
  vi.resetAllMocks();
});

describe("GET list routes", () => {
  it.each([
    ["@/app/api/connectors/bindings/route", "/api/connectors/bindings", "/connectors/bindings"],
    ["@/app/api/connectors/reads/route", "/api/connectors/reads", "/connectors/reads"],
    ["@/app/api/deploys/targets/route", "/api/deploys/targets", "/deploys/targets"],
    ["@/app/api/deploys/runs/route", "/api/deploys/runs", "/deploys/runs"],
  ])("%s: 401 without a cookie, 400 without workspace, forwards with the token", async (mod, path, backend) => {
    const { GET } = await import(mod);

    cookiesMock.mockResolvedValue(cookieStore());
    let fetchMock = stubBackend();
    expect((await GET(get(`${path}?workspace=${WS}`))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();

    cookiesMock.mockResolvedValue(cookieStore("token"));
    fetchMock = stubBackend();
    expect((await GET(get(path))).status).toBe(400);
    expect((await GET(get(`${path}?workspace=w1%26x%3D1`))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();

    fetchMock = stubBackend(200, [{ id: "x" }]);
    const response = await GET(get(`${path}?workspace=${WS}&ignored=1`));
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toMatch(new RegExp(`${backend}\\?workspace=${WS}$`));
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual([{ id: "x" }]);
  });

  it("forwards the optional filters and validates the run target as a UUID", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend(200, []);
    const runs = await import("@/app/api/deploys/runs/route");
    expect((await runs.GET(get(`/api/deploys/runs?workspace=${WS}&target=nope`))).status).toBe(400);
    await runs.GET(get(`/api/deploys/runs?workspace=${WS}&target=${ID}`));
    expect(fetchMock.mock.calls[0]![0]).toContain(`target=${ID}`);
    const reads = await import("@/app/api/connectors/reads/route");
    await reads.GET(get(`/api/connectors/reads?workspace=${WS}&connector=github`));
    expect(fetchMock.mock.calls[1]![0]).toContain("connector=github");
  });

  it("forwards a backend 403 as-is", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    stubBackend(403, { code: "owner_required" });
    const { GET } = await import("@/app/api/connectors/bindings/route");
    const response = await GET(get(`/api/connectors/bindings?workspace=${WS}`));
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ code: "owner_required" });
  });
});

describe("mutation routes", () => {
  const cases: [string, string, string, unknown, Record<string, string>, string][] = [
    ["@/app/api/deploys/requests/route", "POST", "/api/deploys/requests", { target: ID, sha: "abc" }, {}, "/deploys/requests"],
    ["@/app/api/deploys/targets/route", "POST", "/api/deploys/targets", { name: "x" }, {}, "/deploys/targets"],
    ["@/app/api/deploys/targets/[targetId]/route", "PATCH", `/api/deploys/targets/${ID}`, { enabled: true }, { targetId: ID }, `/deploys/targets/${ID}`],
    ["@/app/api/connectors/bindings/[connector]/route", "PUT", "/api/connectors/bindings/github", { workspace: WS, enabled: true }, { connector: "github" }, "/connectors/bindings/github"],
    ["@/app/api/harness/implementation-jobs/[jobId]/pull-request/route", "POST", `/api/harness/implementation-jobs/${ID}/pull-request`, { title: "t" }, { jobId: ID }, `/harness/implementation-jobs/${ID}/pull-request`],
  ];

  it.each(cases)("%s %s: origin, content type, auth, then forwards the body", async (mod, method, path, body, routeParams, backend) => {
    const handler = (await import(mod))[method] as (r: NextRequest, c: unknown) => Promise<Response>;

    cookiesMock.mockResolvedValue(cookieStore("token"));
    let fetchMock = stubBackend();
    let response = await handler(mutate(path, method, body, { origin: "https://evil.example" }), p(routeParams));
    expect(response.status).toBe(403);
    response = await handler(mutate(path, method, body, { "content-type": "text/plain" }), p(routeParams));
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();

    cookiesMock.mockResolvedValue(cookieStore());
    response = await handler(mutate(path, method, body), p(routeParams));
    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();

    cookiesMock.mockResolvedValue(cookieStore("token"));
    fetchMock = stubBackend(201, { id: "r1" });
    response = await handler(mutate(path, method, body), p(routeParams));
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toMatch(new RegExp(`${backend.replace(/[/]/g, "\\/")}$`));
    expect(init?.method).toBe(method);
    expect(JSON.parse(init?.body as string)).toEqual(body);
    expect(response.status).toBe(201);
  });

  it("bodyless verbs forward a 202 with no body", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend(202);
    const { POST } = await import("@/app/api/deploys/targets/[targetId]/status/route");
    const response = await POST(mutate(`/api/deploys/targets/${ID}/status`, "POST"), p({ targetId: ID }));
    expect(response.status).toBe(202);
    expect(fetchMock.mock.calls[0]![1]?.body).toBeUndefined();
  });
});

describe("path segments", () => {
  it.each(BAD_IDS)("a non-UUID id (%s) is a 404 before cookies or the backend", async (bad) => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend();
    const target = await import("@/app/api/deploys/targets/[targetId]/route");
    const head = await import("@/app/api/deploys/targets/[targetId]/head/route");
    const run = await import("@/app/api/deploys/runs/[runId]/route");
    const cancel = await import("@/app/api/deploys/runs/[runId]/cancel/route");
    const pr = await import("@/app/api/harness/implementation-jobs/[jobId]/pull-request/route");
    const responses = await Promise.all([
      target.GET(get("/x"), p({ targetId: bad })),
      head.GET(get("/x"), p({ targetId: bad })),
      run.GET(get("/x"), p({ runId: bad })),
      cancel.POST(mutate("/x", "POST"), p({ runId: bad })),
      pr.GET(get("/x"), p({ jobId: bad })),
    ]);
    expect(responses.map((r) => r.status)).toEqual([404, 404, 404, 404, 404]);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it.each(["../ai", "GitHub", "a/b", "x".repeat(70)])("a malformed connector name (%s) is a 404", async (bad) => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend();
    const { PUT } = await import("@/app/api/connectors/bindings/[connector]/route");
    const response = await PUT(mutate("/x", "PUT", { enabled: true }), p({ connector: bad }));
    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
