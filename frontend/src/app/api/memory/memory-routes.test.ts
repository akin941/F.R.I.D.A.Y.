import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// Phase 8A branch 6 BFF routes (learning card, ingestion audit view, core
// blocks, sessions, self anchors): auth before backend, Origin/Content-Type
// before cookies on mutations, segments and action names validated before
// interpolation, the backend's status and body forwarded as-is.

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
    ["@/app/api/memory/learning/digest/route", "/api/memory/learning/digest?days=2", {}, "/memory/learning/digest?days=2"],
    ["@/app/api/memory/ingestion/sources/route", `/api/memory/ingestion/sources?workspace=${WS}`, {}, `/memory/ingestion/sources?workspace=${WS}`],
    ["@/app/api/memory/ingestion/sources/[sourceId]/route", `/api/memory/ingestion/sources/${ID}`, { sourceId: ID }, `/memory/ingestion/sources/${ID}`],
    ["@/app/api/memory/ingestion/sources/[sourceId]/rules/route", `/api/memory/ingestion/sources/${ID}/rules`, { sourceId: ID }, `/memory/ingestion/sources/${ID}/rules`],
    ["@/app/api/memory/ingestion/scrub-terms/route", `/api/memory/ingestion/scrub-terms?workspace=${WS}`, {}, `/memory/ingestion/scrub-terms?workspace=${WS}`],
    ["@/app/api/memory/core-blocks/route", `/api/memory/core-blocks?workspace=${WS}`, {}, `/memory/core-blocks?workspace=${WS}`],
    ["@/app/api/memory/core-blocks/[label]/route", `/api/memory/core-blocks/developer?workspace=${WS}`, { label: "developer" }, `/memory/core-blocks/developer?workspace=${WS}`],
    ["@/app/api/memory/core-blocks/[label]/versions/route", `/api/memory/core-blocks/self/versions?workspace=${WS}`, { label: "self" }, `/memory/core-blocks/self/versions?workspace=${WS}`],
    ["@/app/api/memory/sessions/route", `/api/memory/sessions?workspace=${WS}&limit=5`, {}, `/memory/sessions?workspace=${WS}&limit=5`],
    ["@/app/api/memory/self-sources/route", "/api/memory/self-sources", {}, "/ai/self-sources"],
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

  it("requires a UUID workspace where the backend scopes by it", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend();
    const sources = await import("@/app/api/memory/ingestion/sources/route");
    expect((await sources.GET(get("/api/memory/ingestion/sources"))).status).toBe(400);
    expect((await sources.GET(get("/api/memory/ingestion/sources?workspace=nope"))).status).toBe(400);
    const sessions = await import("@/app/api/memory/sessions/route");
    expect((await sessions.GET(get("/api/memory/sessions?workspace=nope"))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects bad path segments before the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend();
    const source = await import("@/app/api/memory/ingestion/sources/[sourceId]/route");
    expect((await source.GET(get("/x"), p({ sourceId: "../ai" }))).status).toBe(404);
    const block = await import("@/app/api/memory/core-blocks/[label]/route");
    expect((await block.GET(get(`/x?workspace=${WS}`), p({ label: "../x" }))).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("mutation routes", () => {
  const cases: [string, string, string, unknown, Record<string, string>, string][] = [
    ["@/app/api/memory/learning/bulk-approve/route", "POST", "/api/memory/learning/bulk-approve", { ids: [ID] }, {}, "/memory/learning/bulk-approve"],
    ["@/app/api/memory/learning/proposals/[kind]/[itemId]/[action]/route", "POST", "/x", { title: "t" }, { kind: "memory", itemId: ID, action: "edit" }, `/memory/learning/proposals/memory/${ID}/edit`],
    ["@/app/api/memory/ingestion/sources/[sourceId]/route", "PATCH", "/x", { enabled: true }, { sourceId: ID }, `/memory/ingestion/sources/${ID}`],
    ["@/app/api/memory/ingestion/sources/[sourceId]/rules/route", "POST", "/x", { rule_type: "repo", pattern: "1", target_workspace: WS }, { sourceId: ID }, `/memory/ingestion/sources/${ID}/rules`],
    ["@/app/api/memory/ingestion/rules/[ruleId]/route", "PATCH", "/x", { enabled: false }, { ruleId: ID }, `/memory/ingestion/rules/${ID}`],
    ["@/app/api/memory/ingestion/scrub-terms/route", "POST", "/x", { workspace: WS, term: "Acme" }, {}, "/memory/ingestion/scrub-terms"],
    ["@/app/api/memory/core-blocks/[label]/route", "PATCH", `/x?workspace=${WS}`, { content: "c" }, { label: "developer" }, `/memory/core-blocks/developer?workspace=${WS}`],
    ["@/app/api/memory/core-blocks/[label]/rollback/route", "POST", `/x?workspace=${WS}`, { version: 2 }, { label: "developer" }, `/memory/core-blocks/developer/rollback?workspace=${WS}`],
  ];

  it.each(cases)("%s %s: origin, content type, auth, then forwards", async (mod, method, path, body, params, backend) => {
    const handler = (await import(mod))[method] as Handler;
    cookiesMock.mockResolvedValue(cookieStore("token"));
    let fetchMock = stubBackend();
    expect((await handler(mutate(path, method, body, { origin: "https://evil.example" }), p(params))).status).toBe(403);
    expect((await handler(mutate(path, method, body, { "content-type": "text/plain" }), p(params))).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();

    cookiesMock.mockResolvedValue(cookieStore());
    expect((await handler(mutate(path, method, body), p(params))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();

    cookiesMock.mockResolvedValue(cookieStore("token"));
    fetchMock = stubBackend(200, { id: "r1" });
    const response = await handler(mutate(path, method, body), p(params));
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url.endsWith(backend)).toBe(true);
    expect(init?.method).toBe(method);
    expect(JSON.parse(init?.body as string)).toEqual(body);
    expect(response.status).toBe(200);
  });

  it("proposal actions: unknown kinds and actions are 404, core-block actions send no body", async () => {
    const { POST } = await import("@/app/api/memory/learning/proposals/[kind]/[itemId]/[action]/route");
    cookiesMock.mockResolvedValue(cookieStore("token"));
    let fetchMock = stubBackend();
    for (const params of [
      { kind: "core-block", itemId: ID, action: "edit" },
      { kind: "memory", itemId: ID, action: "delete" },
      { kind: "task", itemId: ID, action: "approve" },
      { kind: "memory", itemId: "nope", action: "approve" },
      { kind: "memory", itemId: ID, action: "toString" },
    ]) {
      expect((await POST(mutate("/x", "POST", {}), p(params))).status).toBe(404);
    }
    expect(fetchMock).not.toHaveBeenCalled();

    fetchMock = stubBackend(200, { id: ID });
    await POST(mutate("/x", "POST"), p({ kind: "core-block", itemId: ID, action: "approve" }));
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url.endsWith(`/memory/learning/proposals/core-block/${ID}/approve`)).toBe(true);
    expect(init?.body).toBeUndefined();
  });

  it("forget/restore: only those two actions, no body", async () => {
    const { POST } = await import("@/app/api/memory/ingestion/sources/[sourceId]/[action]/route");
    cookiesMock.mockResolvedValue(cookieStore("token"));
    let fetchMock = stubBackend();
    expect((await POST(mutate("/x", "POST"), p({ sourceId: ID, action: "purge" }))).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock = stubBackend(200, { episodes: 2 });
    for (const action of ["forget", "restore"]) {
      await POST(mutate("/x", "POST"), p({ sourceId: ID, action }));
    }
    expect(fetchMock.mock.calls.map(([url]) => url.split("/").pop())).toEqual(["forget", "restore"]);
    expect(fetchMock.mock.calls.every(([, init]) => init?.body === undefined)).toBe(true);
  });

  it("core-block writes require a UUID workspace", async () => {
    const { PATCH } = await import("@/app/api/memory/core-blocks/[label]/route");
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend();
    expect((await PATCH(mutate("/x", "PATCH", { content: "c" }), p({ label: "developer" }))).status).toBe(400);
    expect((await PATCH(mutate("/x?workspace=bad", "PATCH", { content: "c" }), p({ label: "developer" }))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("deletes a scrub term (origin checked, 204 forwarded)", async () => {
    const { DELETE } = await import("@/app/api/memory/ingestion/scrub-terms/[termId]/route");
    cookiesMock.mockResolvedValue(cookieStore("token"));
    let fetchMock = stubBackend(204);
    expect((await DELETE(mutate("/x", "DELETE", undefined, { origin: "https://evil.example" }), p({ termId: ID }))).status).toBe(403);
    expect((await DELETE(mutate("/x", "DELETE"), p({ termId: "nope" }))).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock = stubBackend(204);
    const response = await DELETE(mutate("/x", "DELETE"), p({ termId: ID }));
    expect(response.status).toBe(204);
    expect(fetchMock.mock.calls[0]![1]?.method).toBe("DELETE");
  });
});
