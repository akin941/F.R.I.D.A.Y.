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

function params(taskId: string, action: string) {
  return Promise.resolve({ taskId, action });
}

function postRequest(
  taskId: string,
  action: string,
  headers: Record<string, string> = {},
) {
  return new NextRequest(`http://localhost:3001/api/tasks/${taskId}/${action}`, {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
  });
}

const INJECTION_IDS = ["../ai/conversations", "not-a-uuid", "11111111-1111-1111-1111-11111111111g"];

describe("POST /api/tasks/[taskId]/[action]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/tasks/[taskId]/[action]/route");
    const response = await POST(postRequest(VALID_ID, "complete", { origin: "https://evil.example" }), {
      params: params(VALID_ID, "complete"),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/tasks/[taskId]/[action]/route");
    const response = await POST(postRequest(VALID_ID, "complete", { "content-type": "text/plain" }), {
      params: params(VALID_ID, "complete"),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 404 for an unknown action before touching cookies or the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/tasks/[taskId]/[action]/route");
    const response = await POST(postRequest(VALID_ID, "promote"), {
      params: params(VALID_ID, "promote"),
    });

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it.each(INJECTION_IDS)(
    "rejects a non-UUID taskId (%s) with 404 before touching cookies or the backend",
    async (taskId) => {
      cookiesMock.mockResolvedValue(cookieStore("token"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const { POST } = await import("@/app/api/tasks/[taskId]/[action]/route");
      const response = await POST(postRequest(taskId, "complete"), {
        params: params(taskId, "complete"),
      });

      expect(response.status).toBe(404);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(cookiesMock).not.toHaveBeenCalled();
    },
  );

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/tasks/[taskId]/[action]/route");
    const response = await POST(postRequest(VALID_ID, "complete"), {
      params: params(VALID_ID, "complete"),
    });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the transitioned task on success", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ id: "t1", status: "done" }), { status: 200 })),
    );

    const { POST } = await import("@/app/api/tasks/[taskId]/[action]/route");
    const response = await POST(postRequest(VALID_ID, "complete"), {
      params: params(VALID_ID, "complete"),
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ id: "t1", status: "done" });
  });

  it("forwards a 400 for an illegal transition", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "Cannot cancel a done task." }), { status: 400 }),
      ),
    );

    const { POST } = await import("@/app/api/tasks/[taskId]/[action]/route");
    const response = await POST(postRequest(VALID_ID, "cancel"), {
      params: params(VALID_ID, "cancel"),
    });

    expect(response.status).toBe(400);
  });

  it("forwards restore (ADR-009 trash) to the backend's restore view", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify({ id: VALID_ID, deleted_at: null }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/tasks/[taskId]/[action]/route");
    const response = await POST(postRequest(VALID_ID, "restore"), {
      params: params(VALID_ID, "restore"),
    });

    expect(fetchMock.mock.calls[0]?.[0]).toMatch(new RegExp(`/tasks/${VALID_ID}/restore$`));
    expect(response.status).toBe(200);
  });
});
