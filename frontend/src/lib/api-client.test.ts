import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "@/lib/api-client";

describe("apiFetch", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("on a single 401 it refreshes once and retries the original request exactly once", async () => {
    let dataCalls = 0;
    let refreshCalls = 0;
    const fetchMock = vi.fn(async (input: string) => {
      if (input === "/api/auth/refresh") {
        refreshCalls += 1;
        return new Response(null, { status: 200 });
      }
      if (input === "/api/data") {
        dataCalls += 1;
        return dataCalls === 1
          ? new Response(null, { status: 401 })
          : new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      throw new Error(`unexpected fetch: ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const response = await apiFetch("/api/data");

    expect(response.status).toBe(200);
    expect(dataCalls).toBe(2);
    expect(refreshCalls).toBe(1);
  });

  it("coalesces concurrent 401s into a single refresh call", async () => {
    let dataCalls = 0;
    let refreshCalls = 0;
    const fetchMock = vi.fn(async (input: string) => {
      if (input === "/api/auth/refresh") {
        refreshCalls += 1;
        return new Response(null, { status: 200 });
      }
      if (input === "/api/data") {
        dataCalls += 1;
        // The first two concurrent callers both observe a 401; only calls
        // after the refresh resolves should succeed.
        return dataCalls <= 2
          ? new Response(null, { status: 401 })
          : new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      throw new Error(`unexpected fetch: ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const [first, second] = await Promise.all([apiFetch("/api/data"), apiFetch("/api/data")]);

    expect(first.status).toBe(200);
    expect(second.status).toBe(200);
    expect(refreshCalls).toBe(1);
  });

  it("does not enter a retry loop when refresh fails, and redirects to login", async () => {
    // Assigning window.location directly makes jsdom attempt a real
    // navigation; stubbing the global `location` binding instead avoids that
    // while still being the same object apiFetch reads via `window.location`.
    const locationStub = { href: "" };
    vi.stubGlobal("location", locationStub);

    let dataCalls = 0;
    const fetchMock = vi.fn(async (input: string) => {
      if (input === "/api/auth/refresh") {
        return new Response(null, { status: 401 });
      }
      if (input === "/api/data") {
        dataCalls += 1;
        return new Response(null, { status: 401 });
      }
      throw new Error(`unexpected fetch: ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const response = await apiFetch("/api/data");

    expect(response.status).toBe(401);
    expect(dataCalls).toBe(1);
    expect(locationStub.href).toBe("/login");
  });

  it("redirects to login if the retried request is still 401 after a successful refresh", async () => {
    // Refresh succeeding but the retried request still 401ing (e.g. the
    // session was revoked between the two calls) is not a transient failure
    // to retry again — it must be treated the same as a failed refresh.
    const locationStub = { href: "" };
    vi.stubGlobal("location", locationStub);

    let dataCalls = 0;
    let refreshCalls = 0;
    const fetchMock = vi.fn(async (input: string) => {
      if (input === "/api/auth/refresh") {
        refreshCalls += 1;
        return new Response(null, { status: 200 });
      }
      if (input === "/api/data") {
        dataCalls += 1;
        return new Response(null, { status: 401 });
      }
      throw new Error(`unexpected fetch: ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const response = await apiFetch("/api/data");

    expect(response.status).toBe(401);
    expect(dataCalls).toBe(2);
    expect(refreshCalls).toBe(1);
    expect(locationStub.href).toBe("/login");
  });
});
