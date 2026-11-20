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

const CONVERSATION = "11111111-2222-4333-8444-555555555555";
const TRACE = "6f1c2d3e-4b5a-4c6d-8e7f-0123456789ab";

function request(query: string) {
  return new NextRequest(`http://localhost:3001/api/voice/receipt?${query}`);
}

describe("GET /api/voice/receipt", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("401 without a session, before the backend", async () => {
    cookiesMock.mockReturnValue(cookieStore());
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("@/app/api/voice/receipt/route");
    const response = await GET(request(`conversation=${CONVERSATION}`));
    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([`conversation=nope`, `conversation=${CONVERSATION}&trace=bad`, ""])(
    "rejects a non-UUID query (%s) before the backend",
    async (query) => {
      cookiesMock.mockReturnValue(cookieStore("tok"));
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);
      const { GET } = await import("@/app/api/voice/receipt/route");
      expect((await GET(request(query))).status).toBe(400);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("forwards only the conversation and trace and returns the receipt", async () => {
    cookiesMock.mockReturnValue(cookieStore("tok"));
    const body = { turn: null, today: { calls: 0 }, currency: "USD" };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { GET } = await import("@/app/api/voice/receipt/route");
    const response = await GET(request(`conversation=${CONVERSATION}&trace=${TRACE}&extra=x`));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual(body);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(
      `http://127.0.0.1:8001/api/v1/voice/receipt?conversation=${CONVERSATION}&trace=${TRACE}`,
    );
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });

  it("passes the backend's error status through", async () => {
    cookiesMock.mockReturnValue(cookieStore("tok"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ code: "channel_forbidden" }), { status: 403 })),
    );
    const { GET } = await import("@/app/api/voice/receipt/route");
    const response = await GET(request(`conversation=${CONVERSATION}`));
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ code: "channel_forbidden" });
  });
});
