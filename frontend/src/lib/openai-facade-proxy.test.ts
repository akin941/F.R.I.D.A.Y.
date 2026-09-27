import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const TOKEN = "jpat_valid-looking-token";

function completionRequest(headers: Record<string, string> = {}, body: unknown = { model: "jarvis" }) {
  return new NextRequest("http://localhost:3001/v1/chat/completions", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

function modelsRequest(headers: Record<string, string> = {}) {
  return new NextRequest("http://localhost:3001/v1/models", { headers });
}

async function load(env: Record<string, string> = {}) {
  vi.resetModules();
  for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);
  const proxy = await import("@/lib/openai-facade-proxy");
  proxy.resetFacadeLimiter();
  const completions = await import("@/app/v1/chat/completions/route");
  const models = await import("@/app/v1/models/route");
  return { completions, models };
}

function upstream(status: number, body: BodyInit | null, headers: Record<string, string> = {}) {
  return new Response(body, { status, headers });
}

describe("OpenAI-compatible facade passthrough (/v1)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("forwards only the bearer token to Django and streams the body back", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"object":"chat.completion.chunk"}\n\n'));
        controller.enqueue(encoder.encode("data: [DONE]\n\n"));
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(
      upstream(200, stream, {
        "content-type": "text/event-stream",
        "x-jarvis-conversation-id": "c1",
        "set-cookie": "leak=1",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { completions } = await load();

    const response = await completions.POST(
      completionRequest({
        authorization: `Bearer ${TOKEN}`,
        cookie: "jarvis_access=secret-session",
        origin: "https://evil.example",
        "x-jarvis-conversation-id": "c1",
        accept: "text/event-stream",
      }),
    );

    expect(response.status).toBe(200);
    expect(await response.text()).toBe('data: {"object":"chat.completion.chunk"}\n\ndata: [DONE]\n\n');
    expect(response.headers.get("x-jarvis-conversation-id")).toBe("c1");
    expect(response.headers.get("cache-control")).toBe("no-cache");
    // No cookies out, no CORS headers.
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("access-control-allow-origin")).toBeNull();

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://127.0.0.1:8001/api/v1/openai/chat/completions");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({
      Authorization: `Bearer ${TOKEN}`,
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      "X-Jarvis-Conversation-Id": "c1",
    });
    expect(JSON.parse(init.body)).toEqual({ model: "jarvis" });
  });

  it("forwards GET /v1/models", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(upstream(200, '{"object":"list","data":[]}', { "content-type": "application/json" }));
    vi.stubGlobal("fetch", fetchMock);
    const { models } = await load();

    const response = await models.GET(modelsRequest({ authorization: `Bearer ${TOKEN}` }));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ object: "list", data: [] });
    expect(fetchMock.mock.calls[0]![0]).toBe("http://127.0.0.1:8001/api/v1/openai/models");
    expect(fetchMock.mock.calls[0]![1].body).toBeUndefined();
  });

  it.each([
    ["no Authorization header", {}],
    ["a session cookie only", { cookie: "jarvis_access=secret-session" }],
    ["a non-PAT bearer (a JWT)", { authorization: "Bearer eyJhbGciOi.x.y" }],
    ["a non-bearer scheme", { authorization: `Basic ${TOKEN}` }],
  ])("answers 401 itself for %s, without calling Django", async (_label, headers) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { completions } = await load();

    const response = await completions.POST(completionRequest(headers));

    expect(response.status).toBe(401);
    expect(response.headers.get("www-authenticate")).toBe("Bearer");
    expect((await response.json()).error.code).toBe("invalid_api_key");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("stops an invalid-token flood before Django (global bucket without a trusted IP)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(upstream(401, '{"error":{}}'));
    vi.stubGlobal("fetch", fetchMock);
    const { completions } = await load({ FACADE_PREAUTH_FAILURES_GLOBAL: "3" });

    for (let i = 0; i < 3; i += 1) {
      const response = await completions.POST(completionRequest({ authorization: `Bearer jpat_bad${i}` }));
      expect(response.status).toBe(401);
    }
    const blocked = await completions.POST(completionRequest({ authorization: `Bearer ${TOKEN}` }));

    expect(blocked.status).toBe(429);
    expect(Number(blocked.headers.get("retry-after"))).toBeGreaterThanOrEqual(1);
    expect((await blocked.json()).error.code).toBe("rate_limit_exceeded");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("limits per trusted client IP when the proxy header is configured", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async (_url: string, init: { headers: Record<string, string> }) =>
        init.headers.Authorization === `Bearer ${TOKEN}`
          ? upstream(200, "{}", { "content-type": "application/json" })
          : upstream(401, "{}"),
      );
    vi.stubGlobal("fetch", fetchMock);
    const { completions } = await load({
      TRUSTED_CLIENT_IP_HEADER: "X-Real-IP",
      FACADE_PREAUTH_FAILURES_PER_SOURCE: "2",
    });

    for (let i = 0; i < 2; i += 1) {
      await completions.POST(
        completionRequest({ authorization: `Bearer jpat_bad${i}`, "x-real-ip": "203.0.113.9" }),
      );
    }
    const attacker = await completions.POST(
      completionRequest({ authorization: `Bearer ${TOKEN}`, "x-real-ip": "203.0.113.9" }),
    );
    const owner = await completions.POST(
      completionRequest({ authorization: `Bearer ${TOKEN}`, "x-real-ip": "192.168.1.5" }),
    );

    expect(attacker.status).toBe(429);
    expect(owner.status).toBe(200);
  });

  it("does not count successful or non-auth errors as failures", async () => {
    const fetchMock = vi.fn().mockResolvedValue(upstream(429, "{}", { "retry-after": "7" }));
    vi.stubGlobal("fetch", fetchMock);
    const { completions } = await load({ FACADE_PREAUTH_FAILURES_GLOBAL: "1" });

    const first = await completions.POST(completionRequest({ authorization: `Bearer ${TOKEN}` }));
    const second = await completions.POST(completionRequest({ authorization: `Bearer ${TOKEN}` }));

    expect(first.status).toBe(429);
    expect(first.headers.get("retry-after")).toBe("7");
    expect(second.status).toBe(429);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("refuses an oversized body without calling Django", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { completions } = await load({ FACADE_MAX_BODY_BYTES: "10" });

    const response = await completions.POST(
      completionRequest({ authorization: `Bearer ${TOKEN}` }, { model: "jarvis", messages: ["x".repeat(50)] }),
    );

    expect(response.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("maps an unreachable backend to a 502 in OpenAI's error shape", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const { models } = await load();

    const response = await models.GET(modelsRequest({ authorization: `Bearer ${TOKEN}` }));

    expect(response.status).toBe(502);
    expect((await response.json()).error.type).toBe("server_error");
  });
});
