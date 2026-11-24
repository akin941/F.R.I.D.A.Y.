import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// Phase 12 branch 5 BFF routes (/api/skills*): auth before the backend,
// Origin before cookies on mutations, ids and the workspace validated
// before interpolation, the upload capped and multipart-only.

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

function mutate(path: string, body?: unknown, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001${path}`, {
    method: "POST",
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

describe("skills BFF routes", () => {
  it("GET list: 401 without a cookie, 400 on a bad workspace, forwards status", async () => {
    const { GET } = (await import("@/app/api/skills/route")) as { GET: Handler };
    cookiesMock.mockResolvedValue(cookieStore());
    let fetchMock = stubBackend();
    expect((await GET(get(`/api/skills?workspace=${WS}`), p({}))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();

    cookiesMock.mockResolvedValue(cookieStore("token"));
    expect((await GET(get("/api/skills?workspace=nope"), p({}))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();

    fetchMock = stubBackend(200, { skills: [] });
    const response = await GET(get(`/api/skills?workspace=${WS}&status=active&x=1`), p({}));
    expect(response.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url.endsWith(`/skills?workspace=${WS}&status=active`)).toBe(true);
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer token");
  });

  it("GET detail: a non-UUID id is a 404 without a backend call", async () => {
    const { GET } = (await import("@/app/api/skills/[skillId]/route")) as { GET: Handler };
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = stubBackend();
    expect((await GET(get("/api/skills/..%2Fx"), p({ skillId: "../x" }))).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  const cases: [string, string, Record<string, string>, unknown, string][] = [
    ["@/app/api/skills/route", `/api/skills?workspace=${WS}`, {}, { text: "x" }, `/skills?workspace=${WS}`],
    ["@/app/api/skills/[skillId]/versions/route", `/api/skills/${ID}/versions`, { skillId: ID }, { text: "x" }, `/skills/${ID}/versions`],
    ["@/app/api/skills/[skillId]/archive/route", `/api/skills/${ID}/archive`, { skillId: ID }, undefined, `/skills/${ID}/archive`],
    ["@/app/api/skills/[skillId]/restore/route", `/api/skills/${ID}/restore`, { skillId: ID }, undefined, `/skills/${ID}/restore`],
    ["@/app/api/skills/versions/[versionId]/approve/route", `/api/skills/versions/${ID}/approve`, { versionId: ID }, { sha256: "a" }, `/skills/versions/${ID}/approve`],
    ["@/app/api/skills/versions/[versionId]/edit-approve/route", `/api/skills/versions/${ID}/edit-approve`, { versionId: ID }, { sha256: "a", text: "x" }, `/skills/versions/${ID}/edit-approve`],
    ["@/app/api/skills/versions/[versionId]/reject/route", `/api/skills/versions/${ID}/reject`, { versionId: ID }, {}, `/skills/versions/${ID}/reject`],
  ];

  it.each(cases)("%s: cross-origin refused, then forwarded", async (mod, path, params, body, backend) => {
    const handler = ((await import(mod)) as Record<string, Handler>).POST!;
    cookiesMock.mockResolvedValue(cookieStore("token"));
    let fetchMock = stubBackend();
    const foreign = await handler(mutate(path, body, { origin: "https://evil.example" }), p(params));
    expect(foreign.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();

    fetchMock = stubBackend(201, { id: "v1" });
    const response = await handler(mutate(path, body), p(params));
    expect(response.status).toBe(201);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url.endsWith(backend)).toBe(true);
    expect(init?.method).toBe("POST");
  });

  it.each(cases.filter(([, , params]) => Object.keys(params).length > 0))(
    "%s: a non-UUID id is a 404",
    async (mod, _path, params) => {
      const handler = ((await import(mod)) as Record<string, Handler>).POST!;
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = stubBackend();
      const key = Object.keys(params)[0]!;
      const response = await handler(mutate("/api/skills/x", {}), p({ [key]: "not-a-uuid" }));
      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("upload: multipart only, workspace required, too large refused, forwarded", async () => {
    const { POST } = (await import("@/app/api/skills/upload/route")) as { POST: Handler };
    cookiesMock.mockResolvedValue(cookieStore("token"));
    let fetchMock = stubBackend();
    expect((await POST(mutate(`/api/skills/upload?workspace=${WS}`, {}), p({}))).status).toBe(403);

    const form = new FormData();
    form.set("file", new Blob(["---\nname: ab\n---\nb"], { type: "text/markdown" }), "SKILL.md");
    const request = (path: string, data: FormData | Blob) => {
      const r = new NextRequest(`http://localhost:3001${path}`, {
        method: "POST",
        headers: { origin: "http://localhost:3001", "content-type": "multipart/form-data; boundary=x" },
        body: data instanceof FormData ? "x" : data,
      });
      if (data instanceof FormData) r.formData = async () => data;
      return r;
    };
    expect((await POST(request("/api/skills/upload", form), p({}))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();

    const big = new Blob([new Uint8Array(1024 * 1024 + 64 * 1024 + 1)]);
    const tooLarge = await POST(request(`/api/skills/upload?workspace=${WS}`, big), p({}));
    // Refused before the backend (counted off the wire, or unparseable).
    expect(tooLarge.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();

    fetchMock = stubBackend(201, { id: "v1" });
    const ok = await POST(request(`/api/skills/upload?workspace=${WS}`, form), p({}));
    expect(ok.status).toBe(201);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url.endsWith(`/skills/upload?workspace=${WS}`)).toBe(true);
    expect(init?.body).toBeInstanceOf(FormData);
  });
});
