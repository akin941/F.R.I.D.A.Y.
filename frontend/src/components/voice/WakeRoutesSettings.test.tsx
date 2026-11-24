import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WakeRoutesSettings } from "@/components/voice/WakeRoutesSettings";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: { id: "w1", name: "Kişisel", type: "personal", role: "owner" } }),
}));

type Handler = (init?: RequestInit) => Response;

function stub(routes: Record<string, Handler | Handler[]>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
      const key = `${init?.method ?? "GET"} ${url}`;
      const entry = routes[key];
      if (!entry) throw new Error(`unexpected ${key}`);
      const handler: Handler = Array.isArray(entry) ? (entry.length > 1 ? entry.shift()! : entry[0]!) : entry;
      return handler(init);
    }),
  );
  return calls;
}

const json = (body: unknown, status = 200) => () => new Response(JSON.stringify(body), { status });

const LIMITS = { aliases_per_route: 5, min_chars: 2, max_chars: 40 };

function list(aliases: { id: string; phrase: string }[] = []) {
  return {
    workspace: "w1",
    limits: LIMITS,
    routes: [
      {
        name: "digest.today",
        effect: "read",
        phrases: ["günaydın", "brifing"],
        aliases: aliases.map((a) => ({ ...a, route: "digest.today", created_at: "2026-11-20T07:00:00Z" })),
      },
      { name: "notices.read", effect: "read", phrases: ["bildirimler"], aliases: [] },
    ],
  };
}

const LIST_URL = "GET /api/assistant/wake-routes?workspace=w1";

afterEach(() => vi.unstubAllGlobals());

describe("WakeRoutesSettings", () => {
  it("lists the routes with their phrases and aliases", async () => {
    stub({ [LIST_URL]: json(list([{ id: "a1", phrase: "kahve zamanı" }])) });
    render(<WakeRoutesSettings />);
    const card = await screen.findByTestId("wake-route-digest.today");
    expect(within(card).getByText("Bugünün brifingi")).toBeInTheDocument();
    expect(within(card).getByText("günaydın")).toBeInTheDocument();
    expect(within(card).getByText("kahve zamanı")).toBeInTheDocument();
    expect(screen.getByTestId("wake-route-notices.read")).toBeInTheDocument();
  });

  it("adds an alias and reloads", async () => {
    const calls = stub({
      [LIST_URL]: [json(list()), json(list([{ id: "a1", phrase: "kahve zamanı" }]))],
      "POST /api/assistant/wake-routes/aliases?workspace=w1": json(
        { id: "a1", route: "digest.today", phrase: "kahve zamanı", created_at: "x" },
        201,
      ),
    });
    render(<WakeRoutesSettings />);
    const card = await screen.findByTestId("wake-route-digest.today");
    await userEvent.type(within(card).getByLabelText("Bugünün brifingi için yeni takma ad"), "Kahve zamanı");
    await userEvent.click(within(card).getByRole("button", { name: "Ekle" }));
    expect(await within(card).findByText("kahve zamanı")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ route: "digest.today", phrase: "Kahve zamanı" });
  });

  it("shows the conflict message from the error code", async () => {
    stub({
      [LIST_URL]: json(list()),
      "POST /api/assistant/wake-routes/aliases?workspace=w1": json(
        { detail: "clash", code: "wake_alias.conflict" },
        400,
      ),
    });
    render(<WakeRoutesSettings />);
    const card = await screen.findByTestId("wake-route-notices.read");
    await userEvent.type(within(card).getByLabelText("Bildirimler için yeni takma ad"), "günaydın");
    await userEvent.click(within(card).getByRole("button", { name: "Ekle" }));
    expect(await screen.findByText(/zaten kullanılıyor/)).toBeInTheDocument();
  });

  it("deletes an alias", async () => {
    const calls = stub({
      [LIST_URL]: [json(list([{ id: "a1", phrase: "kahve zamanı" }])), json(list())],
      "DELETE /api/assistant/wake-routes/aliases/a1": () => new Response(null, { status: 204 }),
    });
    render(<WakeRoutesSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "kahve zamanı takma adını sil" }));
    await waitFor(() => expect(screen.queryByText("kahve zamanı")).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
  });

  it("disables adding once a route has five aliases", async () => {
    const five = [1, 2, 3, 4, 5].map((n) => ({ id: `a${n}`, phrase: `ad ${n}` }));
    stub({ [LIST_URL]: json(list(five)) });
    render(<WakeRoutesSettings />);
    const card = await screen.findByTestId("wake-route-digest.today");
    expect(within(card).getByLabelText("Bugünün brifingi için yeni takma ad")).toBeDisabled();
  });
});
