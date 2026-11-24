import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ImplementationJobForm } from "@/components/harness/ImplementationJobForm";
import { implJob, jsonResponse, repo } from "@/components/harness/test-fixtures";
import { PlaybookProvenance } from "@/components/skills/SkillDetailView";

// Phase 12 D17: the skill picker on the implementation request and the
// playbook provenance on `/skills/<id>`.

const SHA = "c".repeat(64);

function skillList() {
  const summary = (id: string, name: string, status: string, current: boolean) => ({
    id,
    workspace: "w1",
    name,
    status,
    origin: "playbook",
    use_count: 0,
    last_used_at: null,
    current_version: current
      ? {
          id: `${id}-v1`,
          version: 1,
          sha256: SHA,
          decision: "approved",
          proposed_by: "playbook",
          description: "d",
          created_at: "2026-11-20T07:00:00Z",
          decided_at: null,
        }
      : null,
    pending_versions: 0,
    created_at: "2026-11-20T07:00:00Z",
  });
  return {
    workspace: "w1",
    limits: { max_active: 50, body_max_chars: 16000, bundle_max_bytes: 1048576 },
    skills: [
      summary("s1", "acme-api-migration", "active", true),
      summary("s2", "arsivli", "archived", true),
      summary("s3", "bekleyen", "proposed", false),
    ],
  };
}

describe("ImplementationJobForm skill picker", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("offers only usable skills and posts the selected version ids", async () => {
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (input.startsWith("/api/harness/repos")) return jsonResponse([repo()]);
      if (input.startsWith("/api/skills")) return jsonResponse(skillList());
      if (init?.method === "POST") return jsonResponse(implJob(), 201);
      throw new Error(`unexpected fetch ${input}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onCreated = vi.fn();
    render(<ImplementationJobForm workspaceId="w1" onCreated={onCreated} onCancel={() => undefined} />);

    const box = await screen.findByRole("checkbox", { name: /acme-api-migration/ });
    expect(screen.queryByRole("checkbox", { name: /arsivli/ })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /bekleyen/ })).toBeNull();
    await userEvent.click(box);
    await userEvent.click(await screen.findByRole("combobox", { name: "Depo" }));
    await userEvent.click(await screen.findByRole("option", { name: "friday" }));
    await userEvent.type(screen.getByLabelText("Uygulama hedefi"), "Migration ekle");
    await userEvent.click(screen.getByRole("button", { name: "Uygulama iste" }));

    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    const body = JSON.parse(call?.[1]?.body as string) as Record<string, unknown>;
    expect(body.skill_versions).toEqual(["s1-v1"]);
    expect(onCreated).toHaveBeenCalled();
  });

  it("shows no picker when the workspace has no usable skill", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.startsWith("/api/harness/repos")) return jsonResponse([repo()]);
        if (input.startsWith("/api/skills")) return jsonResponse({ ...skillList(), skills: [] });
        throw new Error(`unexpected fetch ${input}`);
      }),
    );
    render(<ImplementationJobForm workspaceId="w1" onCreated={() => undefined} onCancel={() => undefined} />);
    await screen.findByRole("combobox", { name: "Depo" });
    expect(screen.queryByTestId("impl-job-skills")).toBeNull();
  });
});

describe("PlaybookProvenance", () => {
  it("links every step to its source job and marks paths unverified", () => {
    render(
      <PlaybookProvenance
        provenance={{
          kind: "playbook",
          steps: [
            {
              index: 1,
              source: "11111111-2222-3333-4444-555555555555",
              field: "goal",
              quote: "migration ekle",
              paths: ["backend/app/migrations/0002_x.py"],
            },
            { index: 2, source: "66666666-2222-3333-4444-555555555555", field: "test_command", quote: "make test", paths: [] },
          ],
          unverified_paths: ["backend/app/models.py"],
        }}
      />,
    );
    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/harness/implementations/11111111-2222-3333-4444-555555555555",
      "/harness/implementations/66666666-2222-3333-4444-555555555555",
    ]);
    expect(screen.getByText(/sahibin hedefi: “migration ekle”/)).toBeInTheDocument();
    expect(screen.getByText(/test komutu: “make test”/)).toBeInTheDocument();
    expect(screen.getByText(/Dosya ipuçları \(doğrulanmamış\)/)).toBeInTheDocument();
    expect(screen.getByText(/Doğrulanmamış dosya ipuçları: backend\/app\/models.py/)).toBeInTheDocument();
  });

  it("renders nothing for a non-playbook version", () => {
    const { container } = render(<PlaybookProvenance provenance={{}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
