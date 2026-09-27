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
      name === "jarvis_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

function params(jobId: string = VALID_ID) {
  return Promise.resolve({ jobId });
}

function getRequest(jobId: string = VALID_ID) {
  return new NextRequest(`http://localhost:3001/api/harness/jobs/${jobId}`, { method: "GET" });
}

// Same injection precedent as tasks/memory/knowledge routes: an unvalidated
// dynamic segment interpolated into a server-side, bearer-authenticated
// fetch could redirect the upstream request to an unintended backend path.
const INJECTION_IDS = [
  "../ai/conversations",
  "not-a-uuid",
  "11111111-1111-1111-1111-11111111111g",
];

describe("GET /api/harness/jobs/[jobId]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("@/app/api/harness/jobs/[jobId]/route");
    const response = await GET(getRequest(), { params: params() });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards a 404 from the backend for an out-of-scope job", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 404 })),
    );

    const { GET } = await import("@/app/api/harness/jobs/[jobId]/route");
    const response = await GET(getRequest(), { params: params() });

    expect(response.status).toBe(404);
  });

  it("forwards the job detail body, including briefing, on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const body = {
      id: "j1",
      status: "succeeded",
      briefing: { summary: "All clear.", findings: [], raw_transcript: "..." },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })),
    );

    const { GET } = await import("@/app/api/harness/jobs/[jobId]/route");
    const response = await GET(getRequest(), { params: params() });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(body);
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID jobId (%s) with 404 before touching cookies or the backend",
    async (jobId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { GET } = await import("@/app/api/harness/jobs/[jobId]/route");
      const response = await GET(getRequest(jobId), { params: params(jobId) });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );
});
