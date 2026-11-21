import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { ToolRequestCard, nodeActionDraft, type ToolRequestDraft } from "@/components/chat/ToolRequestCard";
import { tr } from "@/lib/i18n/tr";
import { renderWithClient } from "@/test/render";

const WS = "11111111-1111-4111-8111-111111111111";
const NODE = "22222222-2222-4222-8222-222222222222";

function draft(capability: string, args: Record<string, unknown>): ToolRequestDraft {
  return {
    sourceMessageId: "33333333-3333-4333-8333-333333333333",
    toolName: "node_action",
    payload: { node_id: NODE, capability, args },
  };
}

function mockFetch(body: unknown, status = 201) {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
    async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderCard(value: ToolRequestDraft, onCreated = vi.fn()) {
  renderWithClient(
    <ToolRequestCard draft={value} workspaceId={WS} created={null} onCreated={onCreated} onDiscard={vi.fn()} />,
  );
  return onCreated;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ToolRequestCard for node actions (Phase 10 D12/D13)", () => {
  it("a gate-class draft posts to /approvals with the source message, never to the node", async () => {
    const fetchMock = mockFetch({ id: "r1", tool_name: "node_action", status: "pending" });
    const onCreated = renderCard(draft("repo.command.run", { repo_path: "~/work/acme", command_kind: "test" }));
    const card = screen.getByTestId("tool-request-card");
    expect(card).toHaveAttribute("data-node-class", "gate");
    expect(screen.getByTestId("node-action-draft-summary")).toHaveTextContent("acme");
    expect(screen.getByTestId("node-action-draft-summary")).not.toHaveTextContent("~/work");
    expect(card).toHaveTextContent(tr.chat.toolDraft.nodeAction.whereToAct);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: tr.chat.toolDraft.confirm }));
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/approvals/requests");
    expect(JSON.parse(init!.body as string)).toEqual({
      workspace: WS,
      tool_name: "node_action",
      payload: {
        node_id: NODE,
        capability: "repo.command.run",
        args: { repo_path: "~/work/acme", command_kind: "test" },
        source_message_id: "33333333-3333-4333-8333-333333333333",
      },
    });
    expect(onCreated).toHaveBeenCalled();
  });

  it("a confirm-class draft posts to /nodes/<id>/actions and creates no approval", async () => {
    const fetchMock = mockFetch(
      { id: "e1", node_id: NODE, capability: "apps.open", capability_class: "confirm", status: "sent" },
      202,
    );
    const onCreated = renderCard(draft("apps.open", { bundle_id: "com.apple.Terminal" }));
    expect(screen.getByTestId("tool-request-card")).toHaveAttribute("data-node-class", "confirm");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: tr.chat.toolDraft.nodeAction.sendToMac }));
    });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(`/api/nodes/${NODE}/actions`);
    expect(JSON.parse(init!.body as string)).toEqual({
      capability: "apps.open",
      args: { bundle_id: "com.apple.Terminal" },
      source_message_id: "33333333-3333-4333-8333-333333333333",
    });
    expect(onCreated).not.toHaveBeenCalled();
    expect(screen.getByTestId("node-action-sent")).toHaveTextContent(tr.chat.toolDraft.nodeAction.sent);
  });

  it("apps.focus is confirm-class too; everything else is gate-class", () => {
    expect(nodeActionDraft(draft("apps.focus", {})).confirmClass).toBe(true);
    for (const capability of ["repo.command.run", "applescript.recipe.run", "ax.press", "ax.type_text", ""]) {
      expect(nodeActionDraft(draft(capability, {})).confirmClass).toBe(false);
    }
  });

  it("nothing is sent before the click", () => {
    const fetchMock = mockFetch({});
    renderCard(draft("apps.open", { bundle_id: "com.apple.Terminal" }));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
