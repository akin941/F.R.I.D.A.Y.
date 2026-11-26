import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConnectorsView } from "@/components/connectors/ConnectorsView";
import { binding, jsonResponse } from "@/components/deploys/test-fixtures";
import { calledWith, cancelDialog, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import type { ApiConnectorBinding, ApiConnectorRecentRead } from "@/types/api";

const { holder } = vi.hoisted(() => ({
  holder: { current: { id: "w1", name: "Kişisel", type: "personal", role: "owner" } },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: holder.current }),
}));

function read(overrides: Partial<ApiConnectorRecentRead> = {}): ApiConnectorRecentRead {
  return {
    id: "a1",
    created_at: "2026-11-20T00:00:00Z",
    connector: "github",
    tool: "github_pr_status",
    outcome: "ok",
    channel: "web",
    data_class: "",
    size: 512,
    duration_ms: 40,
    truncated: false,
    ...overrides,
  };
}

function stub(
  bindings: Record<string, ApiConnectorBinding[]>,
  reads: Record<string, ApiConnectorRecentRead[]> = {},
) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const ws = new URL(url, "http://x").searchParams.get("workspace") ?? "";
    if (method === "GET" && url.startsWith("/api/connectors/bindings?")) {
      return jsonResponse(bindings[ws] ?? []);
    }
    if (method === "GET" && url.startsWith("/api/connectors/reads?")) {
      return jsonResponse(reads[ws] ?? []);
    }
    if (method === "PUT" && url.startsWith("/api/connectors/bindings/")) {
      const body = JSON.parse(init!.body as string);
      const current = bindings[body.workspace]![0]!;
      return jsonResponse({ ...current, enabled: body.enabled });
    }
    if (method === "GET" && url.startsWith("/api/connectors/home-assistant/entities?")) {
      return jsonResponse({ workspace: ws, limits: {}, services: {}, results: [] });
    }
    if (method === "GET" && url.startsWith("/api/workspaces/")) {
      return jsonResponse({ workspace: "w1", limits: {}, results: [] });
    }
    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ConnectorsView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    holder.current = { id: "w1", name: "Kişisel", type: "personal", role: "owner" };
  });

  it("shows each binding's state, per-tool pin status and health", async () => {
    stub({
      w1: [
        binding({
          enabled: true,
          tools: [
            { name: "github_pr_status", kind: "read", description: "", pin_status: "ok" },
            { name: "github_open_pr", kind: "write", description: "", pin_status: "mismatch" },
          ],
          health: {
            checked_at: "2026-11-20T00:00:00Z",
            error: "",
            server_name: "github-mcp-server",
            server_version: "1.12.2",
            protocol_version: "2026-07-28",
          },
        }),
        binding({
          connector: "google",
          display_name: "Google (Gmail + Takvim)",
          kind: "native",
          data_class: "personal",
          tools: [],
        }),
      ],
    });
    renderWithClient(<ConnectorsView />);
    const github = await screen.findByTestId("connector-github");
    expect(within(github).getByText("Açık")).toBeInTheDocument();
    expect(within(screen.getByTestId("tool-github_pr_status")).getByText("Doğrulandı")).toBeInTheDocument();
    expect(within(screen.getByTestId("tool-github_open_pr")).getByText("Şema değişti — kapalı")).toBeInTheDocument();
    expect(within(github).getByText(/yeni bir manifest commit'i/)).toBeInTheDocument();
    expect(within(github).getByText("Sunucu: github-mcp-server 1.12.2")).toBeInTheDocument();
    const google = screen.getByTestId("connector-google");
    expect(within(google).getByText("Kişisel veri (yalnızca yerel model)")).toBeInTheDocument();
    expect(within(google).getByText("Kapalı")).toBeInTheDocument();
  });

  it("enabling happens only from the confirmation, with the workspace in the body", async () => {
    const fetchMock = stub({ w1: [binding()] });
    renderWithClient(<ConnectorsView />);
    const card = await screen.findByTestId("connector-github");
    await userEvent.click(within(card).getByRole("button", { name: "Aç" }));
    expect(await findConfirmDialog()).toHaveTextContent("şema doğrulaması");
    expect(calledWith(fetchMock, "PUT", "/api/connectors/bindings/github")).toBe(false);
    await cancelDialog();
    expect(calledWith(fetchMock, "PUT", "/api/connectors/bindings/github")).toBe(false);

    await userEvent.click(within(card).getByRole("button", { name: "Aç" }));
    await confirmInDialog("Aç");
    await waitFor(() => expect(calledWith(fetchMock, "PUT", "/api/connectors/bindings/github")).toBe(true));
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")!;
    expect(JSON.parse(put[1]!.body as string)).toEqual({ workspace: "w1", enabled: true });
  });

  it("a personal connector's enable dialog states the local-model rule", async () => {
    stub({ w1: [binding({ connector: "google", display_name: "Google", data_class: "personal" })] });
    renderWithClient(<ConnectorsView />);
    const card = await screen.findByTestId("connector-google");
    await userEvent.click(within(card).getByRole("button", { name: "Aç" }));
    expect(await findConfirmDialog()).toHaveTextContent("yerel");
  });

  it("a blocked or unbindable connector offers no toggle", async () => {
    stub({
      w1: [
        binding({ connector: "external_installation", display_name: "External installation", status: "blocked", bindable: false, tools: [] }),
      ],
    });
    renderWithClient(<ConnectorsView />);
    const card = await screen.findByTestId("connector-external_installation");
    expect(within(card).getByText("Engelli — bu sürümde açılamaz")).toBeInTheDocument();
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
  });

  it("lists recent reads as bookkeeping (outcome, size, truncation, personal)", async () => {
    stub(
      { w1: [] },
      {
        w1: [
          read(),
          read({ id: "a2", tool: "mail_search", connector: "google", outcome: "timeout", truncated: true, data_class: "personal" }),
        ],
      },
    );
    renderWithClient(<ConnectorsView />);
    const first = await screen.findByTestId("read-a1");
    expect(within(first).getByText("Başarılı")).toBeInTheDocument();
    expect(within(first).getByText(/512 karakter · 40 ms · Web/)).toBeInTheDocument();
    const second = screen.getByTestId("read-a2");
    expect(within(second).getByText("Zaman aşımı")).toBeInTheDocument();
    expect(within(second).getByText(/kırpıldı/)).toBeInTheDocument();
    expect(within(second).getByText("kişisel")).toBeInTheDocument();
  });

  it("switching workspace never shows the previous workspace's bindings", async () => {
    stub({ w1: [binding()], w2: [binding({ connector: "external_installation", display_name: "Ext", status: "blocked", bindable: false })] });
    const { rerender } = renderWithClient(<ConnectorsView />);
    await screen.findByTestId("connector-github");
    holder.current = { id: "w2", name: "Acme", type: "company", role: "owner" };
    rerender(<ConnectorsView />);
    expect(screen.queryByTestId("connector-github")).not.toBeInTheDocument();
    expect(await screen.findByTestId("connector-external_installation")).toBeInTheDocument();
    expect(screen.queryByTestId("connector-github")).not.toBeInTheDocument();
  });

  it("shows the Home Assistant entity allowlist editor only where HA can bind (Phase 13 D11)", async () => {
    stub({
      w1: [binding({ connector: "home_assistant", display_name: "Home Assistant", data_class: "personal" })],
      w2: [binding({ connector: "home_assistant", display_name: "Home Assistant", bindable: false })],
    });
    const { rerender } = renderWithClient(<ConnectorsView />);
    expect(await screen.findByTestId("ha-entities-editor")).toBeInTheDocument();
    expect(await screen.findByTestId("ha-entities-empty")).toBeInTheDocument();
    holder.current = { id: "w2", name: "Acme", type: "company", role: "owner" };
    rerender(<ConnectorsView />);
    await screen.findByTestId("connector-home_assistant");
    expect(screen.queryByTestId("ha-entities-editor")).not.toBeInTheDocument();
  });

  it("shows the owner-only refusal as an error state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ code: "channel_forbidden" }, 403)));
    renderWithClient(<ConnectorsView />);
    expect(await screen.findByText("Bağlantılar yüklenemedi.")).toBeInTheDocument();
  });
});
