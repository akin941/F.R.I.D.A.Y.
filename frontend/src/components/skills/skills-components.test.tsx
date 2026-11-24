import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SkillDetailView } from "@/components/skills/SkillDetailView";
import { SkillsView } from "@/components/skills/SkillsView";
import { routeLabel } from "@/lib/api/wake-routes";
import { diffLines } from "@/lib/skills/diff";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: { id: "w1", name: "Kişisel", type: "personal", role: "owner" } }),
}));

type Handler = (init?: RequestInit) => Response;

function stub(routes: Record<string, Handler | Handler[]>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const raw = init?.body;
      calls.push({
        url,
        method: init?.method ?? "GET",
        body: typeof raw === "string" ? JSON.parse(raw) : raw ?? null,
      });
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

const SHA1 = "a".repeat(64);
const SHA2 = "b".repeat(64);

function version(n: number, decision: string, sha: string, body: string) {
  return {
    id: `v${n}`,
    version: n,
    sha256: sha,
    decision,
    proposed_by: n === 1 ? "owner" : "curator",
    description: "PR yazarken",
    created_at: "2026-11-20T07:00:00Z",
    decided_at: null,
    skill_id: "s1",
    front_matter: { name: "pr-yaz", description: "PR yazarken" },
    body,
    references: {},
    text: `---\nname: "pr-yaz"\n---\n${body}\n`,
    provenance: {},
  };
}

function detail(status = "active") {
  const current = version(1, "approved", SHA1, "Eski satır\nOrtak satır");
  const pending = version(2, "pending", SHA2, "Yeni satır\nOrtak satır");
  return {
    id: "s1",
    workspace: "w1",
    name: "pr-yaz",
    status,
    origin: "upload",
    use_count: 3,
    last_used_at: null,
    current_version: { ...current },
    pending_versions: 1,
    created_at: "2026-11-20T07:00:00Z",
    versions: [pending, current],
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("diffLines", () => {
  it("marks added, removed and shared lines", () => {
    expect(diffLines("a\nb\nc", "a\nx\nc")).toEqual([
      { op: "same", text: "a" },
      { op: "remove", text: "b" },
      { op: "add", text: "x" },
      { op: "same", text: "c" },
    ]);
    expect(diffLines("", "a")).toEqual([{ op: "add", text: "a" }]);
    expect(diffLines("a\r\n", "a\n")).toEqual([{ op: "same", text: "a" }]);
  });
});

describe("routeLabel", () => {
  it("names skill routes and keeps the code-defined labels", () => {
    expect(routeLabel("digest.today").title).toBe("Bugünün brifingi");
    expect(routeLabel("skill.pr-yaz").title).toBe("Beceri: pr-yaz");
    expect(routeLabel("other.route").title).toBe("other.route");
  });
});

describe("SkillsView", () => {
  it("lists skills by status and proposes a typed SKILL.md", async () => {
    const list = {
      workspace: "w1",
      limits: { max_active: 50, body_max_chars: 16000, bundle_max_bytes: 1048576 },
      skills: [
        { ...detail(), versions: undefined },
        { ...detail("proposed"), id: "s2", name: "taslak", current_version: null, pending_versions: 1 },
      ],
    };
    const calls = stub({
      "GET /api/skills?workspace=w1": json(list),
      "POST /api/skills?workspace=w1": json(version(1, "pending", SHA1, "x"), 201),
    });
    render(<SkillsView />);
    const active = await screen.findByRole("region", { name: "Etkin" });
    expect(within(active).getByText("pr-yaz")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Önerildi" })).getByText("taslak")).toBeInTheDocument();
    expect(screen.getByTestId("skill-pr-yaz").querySelector("a")).toHaveAttribute("href", "/skills/s1");

    await userEvent.click(screen.getByRole("button", { name: "Öner" }));
    expect(await screen.findByText("Önerildi. Onaylayana kadar kullanılmayacak.")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect((post.body as { text: string }).text).toContain("name: yeni-beceri");
  });

  it("uploads a file as multipart", async () => {
    const calls = stub({
      "GET /api/skills?workspace=w1": json({ workspace: "w1", limits: {}, skills: [] }),
      "POST /api/skills/upload?workspace=w1": json(version(1, "pending", SHA1, "x"), 201),
    });
    render(<SkillsView />);
    expect(await screen.findByTestId("skills-empty")).toBeInTheDocument();
    const file = new File(["---\nname: ab\n---\nb"], "SKILL.md", { type: "text/markdown" });
    await userEvent.upload(screen.getByLabelText("SKILL.md ya da .zip"), file);
    await userEvent.click(screen.getByRole("button", { name: "Yükle" }));
    expect(await screen.findByText("Yüklendi. Onaylayana kadar kullanılmayacak.")).toBeInTheDocument();
    const upload = calls.find((c) => c.url.startsWith("/api/skills/upload"))!;
    expect(upload.body).toBeInstanceOf(FormData);
  });

  it("shows the server's refusal", async () => {
    stub({
      "GET /api/skills?workspace=w1": json({ workspace: "w1", limits: {}, skills: [] }),
      "POST /api/skills?workspace=w1": json(
        { detail: "x", code: "skill.invalid", reason: "permission_key_refused" },
        400,
      ),
    });
    render(<SkillsView />);
    await screen.findByTestId("skills-empty");
    await userEvent.click(screen.getByRole("button", { name: "Öner" }));
    expect(await screen.findByText(/SKILL.md kabul edilmedi/)).toBeInTheDocument();
  });
});

describe("SkillDetailView", () => {
  it("shows the diff and approves the pending version by its hash", async () => {
    const calls = stub({
      "GET /api/skills/s1": json(detail()),
      "POST /api/skills/versions/v2/approve": json(version(2, "approved", SHA2, "x")),
    });
    render(<SkillDetailView skillId="s1" />);
    const card = await screen.findByTestId("pending-version-2");
    const diff = within(card).getByTestId("skill-diff");
    expect(diff.textContent).toContain("- Eski satır");
    expect(diff.textContent).toContain("+ Yeni satır");
    expect(diff.textContent).toContain("  Ortak satır");
    expect(within(card).getByText(/Küratör önerisi/)).toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: "Bu sürümü onayla" }));
    expect(await screen.findByText("Onaylandı.")).toBeInTheDocument();
    const approve = calls.find((c) => c.url.endsWith("/approve"))!;
    expect(approve.body).toEqual({ sha256: SHA2 });
    // One item at a time: no bulk action exists on the page.
    expect(screen.queryByRole("button", { name: /tümünü/i })).toBeNull();
  });

  it("edits then approves, rejects, and archives", async () => {
    const calls = stub({
      "GET /api/skills/s1": json(detail()),
      "POST /api/skills/versions/v2/edit-approve": json(version(3, "approved", SHA1, "x")),
      "POST /api/skills/versions/v2/reject": json(version(2, "rejected", SHA2, "x")),
      "POST /api/skills/s1/archive": json(detail("archived")),
    });
    render(<SkillDetailView skillId="s1" />);
    const card = await screen.findByTestId("pending-version-2");
    await userEvent.click(within(card).getByRole("button", { name: "Düzenle" }));
    const editor = within(card).getByLabelText("Düzenlenmiş SKILL.md");
    await userEvent.type(editor, " ek");
    await userEvent.click(within(card).getByRole("button", { name: "Düzenlemeyi onayla" }));
    expect(await screen.findByText("Düzenlendi ve onaylandı.")).toBeInTheDocument();
    const edit = calls.find((c) => c.url.endsWith("/edit-approve"))!;
    expect((edit.body as { sha256: string; text: string }).sha256).toBe(SHA2);
    expect((edit.body as { text: string }).text).toContain(" ek");

    await userEvent.click(within(screen.getByTestId("pending-version-2")).getByRole("button", { name: "Reddet" }));
    expect(await screen.findByText("Reddedildi.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Arşivle" }));
    expect(await screen.findByText("Arşivlendi.")).toBeInTheDocument();
  });

  it("offers restore, not archive, for an archived skill; there is no delete", async () => {
    stub({ "GET /api/skills/s1": json(detail("archived")) });
    render(<SkillDetailView skillId="s1" />);
    expect(await screen.findByRole("button", { name: "Geri al" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Arşivle" })).toBeNull();
    expect(screen.queryByRole("button", { name: /sil/i })).toBeNull();
  });
});
