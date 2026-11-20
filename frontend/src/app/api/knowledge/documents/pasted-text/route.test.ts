import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const cookiesMock = vi.fn();
vi.mock("next/headers", () => ({
  cookies: () => cookiesMock(),
}));

function cookieStore(accessToken?: string) {
  return {
    get: (name: string) =>
      name === "friday_access" && accessToken ? { name, value: accessToken } : undefined,
  };
}

function postRequest(body: unknown, headers: Record<string, string> = {}) {
  return new NextRequest("http://localhost:3001/api/knowledge/documents/pasted-text", {
    method: "POST",
    headers: {
      origin: "http://localhost:3001",
      "content-type": "application/json",
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

describe("POST /api/knowledge/documents/pasted-text", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects a mismatched Origin before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/pasted-text/route");
    const response = await POST(
      postRequest({ workspace: "w1" }, { origin: "https://evil.example" }),
    );

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("rejects a non-JSON Content-Type before checking auth, cookies, or calling the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/pasted-text/route");
    const response = await POST(postRequest({ workspace: "w1" }, { "content-type": "text/plain" }));

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookiesMock).not.toHaveBeenCalled();
  });

  it("returns 401 without calling the backend when there is no access cookie", async () => {
    cookiesMock.mockResolvedValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/pasted-text/route");
    const response = await POST(postRequest({ workspace: "w1" }));

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the created document body and status from the backend", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ id: "d1", title: "Notes", status: "pending" }), {
          status: 201,
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { POST } = await import("@/app/api/knowledge/documents/pasted-text/route");
    const response = await POST(
      postRequest({
        workspace: "w1",
        title: "Notes",
        content: "Some content.",
        sensitivity: "normal",
      }),
    );

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({ id: "d1", title: "Notes", status: "pending" });
  });

  it("forwards a field-keyed validation error from the backend as-is", async () => {
    cookiesMock.mockResolvedValue(cookieStore("token"));
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ content: ["content must not be empty."] }), {
            status: 400,
          }),
      ),
    );

    const { POST } = await import("@/app/api/knowledge/documents/pasted-text/route");
    const response = await POST(postRequest({ workspace: "w1", title: "Notes", content: "" }));

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ content: ["content must not be empty."] });
  });
});
