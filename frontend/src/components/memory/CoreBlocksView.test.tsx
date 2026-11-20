import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CoreBlocksView } from "@/components/memory/CoreBlocksView";
import { calledWith, confirmInDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import type { ApiCoreBlock, ApiCoreBlockVersion, ApiSelfSources } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/memory/core-blocks" }));

const WS = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({
    activeWorkspace: { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "Kişisel", type: "personal", role: "owner" },
    workspaces: [],
  }),
}));

function block(overrides: Partial<ApiCoreBlock>): ApiCoreBlock {
  return {
    label: "developer",
    content: "The owner builds Friday.",
    version: 3,
    sensitivity: "normal",
    updated_by_kind: "owner",
    updated_at: "2026-11-20T08:00:00Z",
    char_budget: 1200,
    editable: true,
    ...overrides,
  };
}

const BLOCKS: ApiCoreBlock[] = [
  block({ label: "self", content: "Ben Friday'yim.", editable: false, updated_by_kind: "assembler", char_budget: 1500 }),
  block({}),
];

const VERSIONS: ApiCoreBlockVersion[] = [
  { version: 3, content: "The owner builds Friday.", sensitivity: "normal", updated_by_kind: "owner", created_at: "2026-11-20T08:00:00Z" },
  { version: 2, content: "Older text.", sensitivity: "normal", updated_by_kind: "approved_proposal", created_at: "2026-11-19T08:00:00Z" },
];

const SOURCES: ApiSelfSources = {
  content_hash: "abc",
  anchors: [
    { group: "identity", label: "", file: "CLAUDE.md", line: 3, text: "Friday OS is a personal-first Personal AI Operating System" },
    { group: "adr", label: "ADR-001: Workspace Is the Data Axis", file: "docs/adr/ADR-001-workspace-axis.md", line: 17, text: "Every business table carries a `workspace_id`." },
  ],
};

function stub() {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(async (input, init) => {
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (input.startsWith("/api/memory/core-blocks?")) return json(BLOCKS);
    if (input.includes("/versions?")) return json(VERSIONS);
    if (input === "/api/memory/self-sources") return json(SOURCES);
    if (init?.method === "PATCH" || input.includes("/rollback?")) return json(block({ version: 4 }));
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CoreBlocksView", () => {
  it("shows self read-only with its source anchors", async () => {
    stub();
    renderWithClient(<CoreBlocksView />);
    const self = await screen.findByTestId("core-block-self");
    expect(self).toHaveAttribute("data-editable", "false");
    expect(within(self).queryByRole("button", { name: "Düzenle" })).not.toBeInTheDocument();
    expect(within(self).queryByRole("textbox")).not.toBeInTheDocument();
    expect(within(self).getByText(/Salt okunur/)).toBeInTheDocument();
    const anchors = await within(self).findByTestId("self-anchors");
    expect(anchors).toHaveTextContent("CLAUDE.md:3");
    expect(anchors).toHaveTextContent("docs/adr/ADR-001-workspace-axis.md:17");
  });

  it("self history has no rollback control", async () => {
    stub();
    renderWithClient(<CoreBlocksView />);
    const self = await screen.findByTestId("core-block-self");
    await userEvent.click(within(self).getByRole("button", { name: "Sürüm geçmişi" }));
    await within(self).findByTestId("core-block-history-self");
    expect(within(self).queryByRole("button", { name: "Bu sürüme dön" })).not.toBeInTheDocument();
  });

  it("edits the developer block with its base version", async () => {
    const fetchMock = stub();
    renderWithClient(<CoreBlocksView />);
    const dev = await screen.findByTestId("core-block-developer");
    await userEvent.click(within(dev).getByRole("button", { name: "Düzenle" }));
    const box = within(dev).getByRole("textbox");
    await userEvent.clear(box);
    await userEvent.type(box, "Sahip, Friday'nin geliştiricisi.");
    await userEvent.click(within(dev).getByRole("button", { name: "Kaydet" }));
    await waitFor(() =>
      expect(calledWith(fetchMock, "PATCH", `/api/memory/core-blocks/developer?workspace=${WS}`)).toBe(true),
    );
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH")!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({
      content: "Sahip, Friday'nin geliştiricisi.",
      sensitivity: "normal",
      base_version: 3,
    });
  });

  it("refuses to save over the character budget", async () => {
    stub();
    renderWithClient(<CoreBlocksView />);
    const dev = await screen.findByTestId("core-block-developer");
    await userEvent.click(within(dev).getByRole("button", { name: "Düzenle" }));
    const box = within(dev).getByRole("textbox");
    await userEvent.clear(box);
    await userEvent.click(box);
    await userEvent.paste("x".repeat(1201));
    expect(within(dev).getByRole("button", { name: "Kaydet" })).toBeDisabled();
    expect(within(dev).getByText(/Karakter sınırı aşıldı/)).toBeInTheDocument();
  });

  it("rolls back to an older version from its confirmation", async () => {
    const fetchMock = stub();
    renderWithClient(<CoreBlocksView />);
    const dev = await screen.findByTestId("core-block-developer");
    await userEvent.click(within(dev).getByRole("button", { name: "Sürüm geçmişi" }));
    const history = await within(dev).findByTestId("core-block-history-developer");
    // Only the older version offers a rollback.
    const buttons = within(history).getAllByRole("button", { name: "Bu sürüme dön" });
    expect(buttons).toHaveLength(1);
    await userEvent.click(buttons[0]!);
    await confirmInDialog("Bu sürüme dön");
    await waitFor(() =>
      expect(calledWith(fetchMock, "POST", `/api/memory/core-blocks/developer/rollback?workspace=${WS}`)).toBe(true),
    );
    const call = fetchMock.mock.calls.find(([url]) => url.includes("/rollback?"))!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({ version: 2 });
  });
});
