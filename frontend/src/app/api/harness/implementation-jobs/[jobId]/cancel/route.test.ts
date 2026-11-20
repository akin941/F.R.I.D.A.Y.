import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

const VALID_ID = "11111111-1111-1111-1111-111111111111";

function cookieStore(accessToken?: string) {
  return {
    get: (name: string) =>
      name === "friday_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

function params(jobId: string = VALID_ID) {
  return Promise.resolve({ jobId });
}

function postRequest(jobId: string = VALID_ID, headers: Record<string, string> = {}) {
  return new NextRequest(`http://localhost:3001/api/harness/implementation-jobs/${jobId}/cancel`, {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
  });
}

const INJECTION_IDS = [
  "../ai/conversations",
  "not-a-uuid",
  "11111111-1111-1111-1111-11111111111g",
];

describe("POST /api/harness/implementation-jobs/[jobId]/cancel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/harness/implementation-jobs/[jobId]/cancel/route");
    const response = await POST(postRequest(VALID_ID, { origin: "https://evil.example" }), {
      params: params(),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/harness/implementation-jobs/[jobId]/cancel/route");
    const response = await POST(postRequest(VALID_ID, { "content-type": "text/plain" }), {
      params: params(),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/harness/implementation-jobs/[jobId]/cancel/route");
    const response = await POST(postRequest(), { params: params() });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the cancelled job on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () =>
        new Response(
          JSON.stringify({ id: "j1", status: "implementing", cancel_requested: true }),
          { status: 200 },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/harness/implementation-jobs/[jobId]/cancel/route");
    const response = await POST(postRequest(), { params: params() });

    expect(fetchMock.mock.calls[0]?.[0]).toMatch(
      new RegExp(`/harness/implementation-jobs/${VALID_ID}/cancel$`),
    );
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("POST");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      id: "j1",
      status: "implementing",
      cancel_requested: true,
    });
  });

  it("forwards a 400 for an invalid transition", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "Job already finished." }), { status: 400 }),
      ),
    );

    const { POST } = await import("@/app/api/harness/implementation-jobs/[jobId]/cancel/route");
    const response = await POST(postRequest(), { params: params() });

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "Job already finished." });
  });

  it("forwards a 403 for a non-curator", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "Role does not allow this." }), { status: 403 }),
      ),
    );

    const { POST } = await import("@/app/api/harness/implementation-jobs/[jobId]/cancel/route");
    const response = await POST(postRequest(), { params: params() });

    expect(response.status).toBe(403);
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID jobId (%s) with 404 before touching cookies or the backend",
    async (jobId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { POST } = await import("@/app/api/harness/implementation-jobs/[jobId]/cancel/route");
      const response = await POST(postRequest(jobId), { params: params(jobId) });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );
});
