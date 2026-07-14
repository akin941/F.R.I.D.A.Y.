import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IntegrationsView } from "@/components/integrations/IntegrationsView";
import type { ApiPersonalAccessToken } from "@/types/api";

const WORKSPACES = [
  { id: "w1", name: "Alice", type: "personal" as const, role: "owner" },
  { id: "w2", name: "Acme", type: "company" as const, role: "member" },
];

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({
    workspaces: WORKSPACES,
    activeWorkspace: WORKSPACES[0],
  }),
}));

function token(overrides: Partial<ApiPersonalAccessToken> = {}): ApiPersonalAccessToken {
  return {
    id: "t1",
    name: "laptop cli",
    workspace: "w1",
    last_used_at: null,
    revoked_at: null,
    created_at: "2026-07-13T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function fetchRouter(handlers: Record<string, () => Response | Promise<Response>>) {
  return vi.fn(async (input: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const key = `${method} ${input.split("?")[0]}`;
    const handler = handlers[key];
    if (handler) return handler();
    if (input.includes("/api/integrations/telegram/link-status")) {
      return jsonResponse({ linked: false, chat_id: null, linked_at: null });
    }
    return jsonResponse([]);
  });
}

describe("IntegrationsView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads and renders the token list", async () => {
    vi.stubGlobal(
      "fetch",
      fetchRouter({
        "GET /api/auth/tokens": () => jsonResponse([token()]),
      }),
    );

    render(<IntegrationsView />);

    expect(await screen.findByText("laptop cli")).toBeInTheDocument();
    // "Alice" also appears in TelegramLinkPanel's own workspace picker.
    expect(screen.getAllByText("Alice").length).toBeGreaterThan(0);
  });

  it("shows an empty state when there are no tokens", async () => {
    vi.stubGlobal(
      "fetch",
      fetchRouter({
        "GET /api/auth/tokens": () => jsonResponse([]),
      }),
    );

    render(<IntegrationsView />);

    expect(await screen.findByText("No personal access tokens yet.")).toBeInTheDocument();
  });

  it("creates a token and reveals its raw value exactly once", async () => {
    const created = { ...token({ id: "new1", name: "phone" }), token: "jpat_raw_value" };
    vi.stubGlobal(
      "fetch",
      fetchRouter({
        "GET /api/auth/tokens": () => jsonResponse([]),
        "POST /api/auth/tokens": () => jsonResponse(created, 201),
      }),
    );

    render(<IntegrationsView />);
    await screen.findByText("No personal access tokens yet.");

    await userEvent.click(screen.getByRole("button", { name: "New token" }));
    await userEvent.type(screen.getByLabelText("Name"), "phone");
    await userEvent.click(screen.getByRole("combobox", { name: "Token workspace" }));
    await userEvent.click(await screen.findByRole("option", { name: "Alice" }));
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));

    expect(await screen.findByText("jpat_raw_value")).toBeInTheDocument();
    expect(await screen.findByText("phone")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText("jpat_raw_value")).not.toBeInTheDocument();
  });

  it("revokes a token", async () => {
    const active = token();
    vi.stubGlobal(
      "fetch",
      fetchRouter({
        "GET /api/auth/tokens": () => jsonResponse([active]),
        "POST /api/auth/tokens/t1/revoke": () =>
          jsonResponse({ ...active, revoked_at: "2026-07-13T01:00:00Z" }),
      }),
    );

    render(<IntegrationsView />);
    await screen.findByText("laptop cli");
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));

    await waitFor(() => {
      expect(screen.getByText(/Revoked/)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
  });
});
