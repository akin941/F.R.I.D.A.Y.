import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CuratorPanel } from "@/components/skills/CuratorPanel";
import { curatorNote } from "@/lib/api/skills";

// Phase 12 branch 6 (D16): the weekly curator's archive suggestions on
// /skills -- the owner archives (one skill) or dismisses; nothing else.

type Handler = (init?: RequestInit) => Response;

function stub(routes: Record<string, Handler | Handler[]>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const raw = init?.body;
      calls.push({ url, method: init?.method ?? "GET", body: typeof raw === "string" ? JSON.parse(raw) : null });
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
const empty = () => new Response(null, { status: 204 });

const RUN = {
  id: "r1",
  week: "2026-W47",
  outcome: "proposals",
  started_at: "2026-11-22T01:15:00Z",
  finished_at: "2026-11-22T01:15:02Z",
  stale_marked: 2,
  reactivated: 0,
  skills_considered: 3,
  proposals_created: 1,
  proposals_dropped: 0,
  drop_reasons: {},
  proposal_version_ids: ["v9"],
};

const SUGGESTION = {
  run_id: "r1",
  skill_id: "s2",
  skill_name: "bosta",
  skill_status: "stale",
  use_count: 0,
  last_used_at: null,
  reason: "unused",
  into_skill_id: null,
  into_skill_name: null,
};

afterEach(() => vi.unstubAllGlobals());

describe("CuratorPanel", () => {
  it("shows the latest run and archives a suggested skill", async () => {
    const onChanged = vi.fn();
    const calls = stub({
      "GET /api/skills/curator?workspace=w1": [
        json({ workspace: "w1", latest_run: RUN, suggestions: [SUGGESTION] }),
        json({ workspace: "w1", latest_run: RUN, suggestions: [] }),
      ],
      "POST /api/skills/s2/archive": json({ id: "s2", status: "archived" }),
    });
    render(<CuratorPanel workspaceId="w1" onChanged={onChanged} />);
    expect(await screen.findByText(/2026-W47: öneri var/)).toBeInTheDocument();
    expect(screen.getByTestId("suggestion-bosta")).toHaveTextContent("Uzun süredir kullanılmıyor");
    await userEvent.click(screen.getByRole("button", { name: "Arşivle" }));
    expect(await screen.findByText("Açık arşiv önerisi yok.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual(["/api/skills/s2/archive"]);
    expect(onChanged).toHaveBeenCalled();
  });

  it("dismisses one suggestion by run and skill", async () => {
    const calls = stub({
      "GET /api/skills/curator?workspace=w1": [
        json({
          workspace: "w1",
          latest_run: RUN,
          suggestions: [{ ...SUGGESTION, reason: "merged", into_skill_name: "pr-yaz", into_skill_id: "s1" }],
        }),
        json({ workspace: "w1", latest_run: RUN, suggestions: [] }),
      ],
      "POST /api/skills/curator/dismiss": empty,
    });
    render(<CuratorPanel workspaceId="w1" />);
    expect(await screen.findByText(/birleştirilmesi önerildi \(pr-yaz\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Yoksay" }));
    expect(await screen.findByText("Açık arşiv önerisi yok.")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toEqual({ run_id: "r1", skill_id: "s2" });
  });

  it("renders nothing before the first run or when the panel cannot load", async () => {
    stub({ "GET /api/skills/curator?workspace=w1": json({ workspace: "w1", latest_run: null, suggestions: [] }) });
    const { container } = render(<CuratorPanel workspaceId="w1" />);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(container).toBeEmptyDOMElement();
  });
});

describe("curatorNote", () => {
  it("describes a curator version in Friday's words only", () => {
    expect(curatorNote({ curator_run_id: "r1", action: "merge", week: "2026-W47" })).toBe(
      "Haftalık küratör birleştirme önerisi · 2026-W47",
    );
    expect(curatorNote({ curator_run_id: "r1", action: "<script>" })).toBe("Haftalık küratör önerisi");
    expect(curatorNote({})).toBeNull();
    expect(curatorNote(null)).toBeNull();
  });
});
