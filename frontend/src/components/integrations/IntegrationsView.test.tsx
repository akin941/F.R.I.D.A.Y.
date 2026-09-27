import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IntegrationsView } from "@/components/integrations/IntegrationsView";
import type { ApiPersonalAccessToken } from "@/types/api";
import { renderWithClient } from "@/test/render";

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
    kind: "cli",
    workspace: "w1",
    last_used_at: null,
    revoked_at: null,
    expires_at: "2999-01-01T00:00:00Z",
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

    renderWithClient(<IntegrationsView />);

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

    renderWithClient(<IntegrationsView />);

    expect(await screen.findByText("Henüz kişisel erişim token'ı yok.")).toBeInTheDocument();
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

    renderWithClient(<IntegrationsView />);
    await screen.findByText("Henüz kişisel erişim token'ı yok.");

    await userEvent.click(screen.getByRole("button", { name: "Yeni token" }));
    await userEvent.type(screen.getByLabelText("İsim"), "phone");
    await userEvent.click(screen.getByRole("combobox", { name: "Token çalışma alanı" }));
    await userEvent.click(await screen.findByRole("option", { name: "Alice" }));
    await userEvent.click(screen.getByRole("button", { name: "Token oluştur" }));

    expect(await screen.findByText("jpat_raw_value")).toBeInTheDocument();
    expect(await screen.findByText("phone")).toBeInTheDocument();
    // Proves the round-1 P2 fix: the raw token is never also stored on the
    // list-row entry -- appears exactly once (the reveal banner), not a
    // second time anywhere the row itself might render it.
    expect(screen.getAllByText("jpat_raw_value")).toHaveLength(1);

    await userEvent.click(screen.getByRole("button", { name: "Tamam" }));
    expect(screen.queryByText("jpat_raw_value")).not.toBeInTheDocument();
  });

  it("resets the reveal banner's copied state for a newly created token", async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    const createdTokens = [
      { ...token({ id: "a", name: "token A" }), token: "jpat_a" },
      { ...token({ id: "b", name: "token B" }), token: "jpat_b" },
    ];
    let createCount = 0;
    vi.stubGlobal(
      "fetch",
      fetchRouter({
        "GET /api/auth/tokens": () => jsonResponse([]),
        "POST /api/auth/tokens": () => jsonResponse(createdTokens[createCount++], 201),
      }),
    );

    renderWithClient(<IntegrationsView />);
    await screen.findByText("Henüz kişisel erişim token'ı yok.");

    await userEvent.click(screen.getByRole("button", { name: "Yeni token" }));
    await userEvent.type(screen.getByLabelText("İsim"), "token A");
    await userEvent.click(screen.getByRole("combobox", { name: "Token çalışma alanı" }));
    await userEvent.click(await screen.findByRole("option", { name: "Alice" }));
    await userEvent.click(screen.getByRole("button", { name: "Token oluştur" }));
    await screen.findByText("jpat_a");

    await userEvent.click(screen.getByRole("button", { name: "Kopyala" }));
    expect(await screen.findByRole("button", { name: "Kopyalandı" })).toBeInTheDocument();

    // Create a second token without dismissing the first's banner.
    await userEvent.click(screen.getByRole("button", { name: "Yeni token" }));
    await userEvent.type(screen.getByLabelText("İsim"), "token B");
    await userEvent.click(screen.getByRole("combobox", { name: "Token çalışma alanı" }));
    await userEvent.click(await screen.findByRole("option", { name: "Alice" }));
    await userEvent.click(screen.getByRole("button", { name: "Token oluştur" }));

    // Proves the round-4 P2 fix: the banner for token B must not inherit
    // token A's "Copied" state -- it hasn't actually been copied yet.
    expect(await screen.findByText("jpat_b")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kopyala" })).toBeInTheDocument();
  });

  it("sends the chosen expiry with the mint request", async () => {
    const created = { ...token({ id: "new1", name: "short" }), token: "jpat_short" };
    const fetchMock = fetchRouter({
      "GET /api/auth/tokens": () => jsonResponse([]),
      "POST /api/auth/tokens": () => jsonResponse(created, 201),
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<IntegrationsView />);
    await screen.findByText("Henüz kişisel erişim token'ı yok.");

    await userEvent.click(screen.getByRole("button", { name: "Yeni token" }));
    await userEvent.type(screen.getByLabelText("İsim"), "short");
    await userEvent.click(screen.getByRole("combobox", { name: "Token çalışma alanı" }));
    await userEvent.click(await screen.findByRole("option", { name: "Alice" }));
    await userEvent.click(screen.getByRole("combobox", { name: "Token geçerlilik süresi" }));
    await userEvent.click(await screen.findByRole("option", { name: "30 gün" }));
    await userEvent.click(screen.getByRole("button", { name: "Token oluştur" }));
    await screen.findByText("jpat_short");

    const postCall = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(postCall![1]!.body as string)).toEqual({
      workspace: "w1",
      name: "short",
      kind: "cli",
      expires_in_days: 30,
    });
  });

  it("defaults the expiry to 90 days", async () => {
    const created = { ...token({ id: "new1", name: "dflt" }), token: "jpat_dflt" };
    const fetchMock = fetchRouter({
      "GET /api/auth/tokens": () => jsonResponse([]),
      "POST /api/auth/tokens": () => jsonResponse(created, 201),
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<IntegrationsView />);
    await screen.findByText("Henüz kişisel erişim token'ı yok.");
    await userEvent.click(screen.getByRole("button", { name: "Yeni token" }));
    await userEvent.type(screen.getByLabelText("İsim"), "dflt");
    await userEvent.click(screen.getByRole("combobox", { name: "Token çalışma alanı" }));
    await userEvent.click(await screen.findByRole("option", { name: "Alice" }));
    await userEvent.click(screen.getByRole("button", { name: "Token oluştur" }));
    await screen.findByText("jpat_dflt");

    const postCall = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(postCall![1]!.body as string).expires_in_days).toBe(90);
  });

  it("shows an expired token as expired, without a revoke button", async () => {
    vi.stubGlobal(
      "fetch",
      fetchRouter({
        "GET /api/auth/tokens": () =>
          jsonResponse([token({ expires_at: "2026-01-01T00:00:00Z" })]),
      }),
    );

    renderWithClient(<IntegrationsView />);
    expect(await screen.findByText(/Süresi doldu/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "İptal et" })).not.toBeInTheDocument();
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

    renderWithClient(<IntegrationsView />);
    await screen.findByText("laptop cli");
    await userEvent.click(screen.getByRole("button", { name: "İptal et" }));

    await waitFor(() => {
      expect(screen.getByText(/İptal edildi/)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "İptal et" })).not.toBeInTheDocument();
  });

  it("mints an OpenAI-compatible token and shows its Open WebUI setup", async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    const created = {
      ...token({ id: "o1", name: "open webui", kind: "openai" }),
      token: "jpat_openai_raw",
    };
    const fetchMock = fetchRouter({
      "GET /api/auth/tokens": () => jsonResponse([]),
      "POST /api/auth/tokens": () => jsonResponse(created, 201),
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<IntegrationsView />);
    await screen.findByText("Henüz kişisel erişim token'ı yok.");
    await userEvent.click(screen.getByRole("button", { name: "Yeni token" }));
    await userEvent.type(screen.getByLabelText("İsim"), "open webui");
    await userEvent.click(screen.getByRole("combobox", { name: "Token türü" }));
    await userEvent.click(await screen.findByRole("option", { name: "OpenAI uyumlu istemci" }));
    expect(screen.getByText(/yalnızca sohbet, araç ve onay yok/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "Token çalışma alanı" }));
    await userEvent.click(await screen.findByRole("option", { name: "Alice" }));
    await userEvent.click(screen.getByRole("button", { name: "Token oluştur" }));

    const setup = await screen.findByLabelText("Open WebUI kurulum bilgisi");
    expect(setup.textContent).toContain(`API Base URL: ${window.location.origin}/v1`);
    expect(setup.textContent).toContain("API Key: jpat_openai_raw");
    expect(setup.textContent).toContain("jarvis-lite");
    await userEvent.click(screen.getByRole("button", { name: "Kurulum bilgisini kopyala" }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(setup.textContent);

    const postCall = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(postCall![1]!.body as string).kind).toBe("openai");
    // The list row shows the kind; the raw token still appears only in the banner.
    expect(await screen.findByText("open webui")).toBeInTheDocument();
    expect(screen.getAllByText("OpenAI uyumlu istemci").length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: "Tamam" }));
    expect(screen.queryByLabelText("Open WebUI kurulum bilgisi")).not.toBeInTheDocument();
  });

  it("shows no setup snippet for a CLI token", async () => {
    const created = { ...token({ id: "c1", name: "cli" }), token: "jpat_cli_raw" };
    vi.stubGlobal(
      "fetch",
      fetchRouter({
        "GET /api/auth/tokens": () => jsonResponse([]),
        "POST /api/auth/tokens": () => jsonResponse(created, 201),
      }),
    );

    renderWithClient(<IntegrationsView />);
    await screen.findByText("Henüz kişisel erişim token'ı yok.");
    await userEvent.click(screen.getByRole("button", { name: "Yeni token" }));
    await userEvent.type(screen.getByLabelText("İsim"), "cli");
    await userEvent.click(screen.getByRole("combobox", { name: "Token çalışma alanı" }));
    await userEvent.click(await screen.findByRole("option", { name: "Alice" }));
    await userEvent.click(screen.getByRole("button", { name: "Token oluştur" }));

    await screen.findByText("jpat_cli_raw");
    expect(screen.queryByLabelText("Open WebUI kurulum bilgisi")).not.toBeInTheDocument();
  });
});
