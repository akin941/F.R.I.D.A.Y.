import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NodesSection } from "@/components/nodes/NodesSection";
import { calledWith, cancelDialog, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import type { ApiNodeDetail, ApiNodeKillSwitch } from "@/types/api";

const ID = "33333333-3333-4333-8333-333333333333";

function node(overrides: Partial<ApiNodeDetail> = {}): ApiNodeDetail {
  return {
    id: ID,
    name: "MacBook Pro",
    status: "connected",
    online: true,
    protocol_version: 1,
    node_version: "0.1.0",
    os_version: "15.1",
    last_heartbeat_at: "2026-11-20T10:00:00Z",
    spool_count: 0,
    spool_bytes: 0,
    paired_at: "2026-11-01T10:00:00Z",
    revoked_at: null,
    credential_age_days: 19,
    credential_expires_at: "2027-01-30T10:00:00Z",
    rotate_after_days: 30,
    pinned_key_id: "abcd1234abcd1234",
    catalogue_sha256: "0123456789ab",
    advertised_count: 3,
    invocable_count: 1,
    snapshot_at: "2026-11-20T10:00:00Z",
    capabilities: [
      { name: "sessions.list", version: 1, class: "read", availability: "granted", invocable: true, reason: null },
      {
        name: "claude.transcripts.read",
        version: 1,
        class: "read",
        availability: "not_granted",
        invocable: false,
        reason: "not_granted",
      },
      { name: "foo.bar", version: 1, class: "read", availability: "granted", invocable: false, reason: "unknown_capability" },
    ],
    ...overrides,
  };
}

const RELEASED: ApiNodeKillSwitch = { engaged: false, engaged_at: null, engaged_via: "", released_at: null };
const ENGAGED: ApiNodeKillSwitch = {
  engaged: true,
  engaged_at: "2026-11-20T11:00:00Z",
  engaged_via: "web",
  released_at: null,
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function stubBackend({
  nodes = [node()],
  killSwitch = RELEASED,
  extra = () => undefined,
}: {
  nodes?: ApiNodeDetail[];
  killSwitch?: ApiNodeKillSwitch;
  extra?: (url: string, init?: RequestInit) => Response | undefined;
} = {}) {
  const state = { killSwitch };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const handled = extra(url, init);
    if (handled) return handled;
    const method = init?.method ?? "GET";
    if (url === "/api/nodes" && method === "GET") {
      return jsonResponse(
        nodes.map((n) => Object.fromEntries(Object.entries(n).filter(([key]) => key !== "capabilities"))),
      );
    }
    if (url === "/api/nodes/kill-switch") {
      if (method === "POST") state.killSwitch = ENGAGED;
      if (method === "DELETE") state.killSwitch = RELEASED;
      return jsonResponse(state.killSwitch);
    }
    const detail = nodes.find((n) => url === `/api/nodes/${n.id}`);
    if (detail && method === "GET") return jsonResponse(detail);
    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("NodesSection (Phase 9 D11)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each([
    ["connected", "Bağlı"],
    ["disconnected", "Bağlı değil"],
    ["kill_switch", "Acil durdurma"],
    ["revoked", "İptal edildi"],
    ["credential_expiring", "Kimlik bilgisi yenilenmeli"],
  ] as const)("renders the %s state as text", async (status, label) => {
    stubBackend({ nodes: [node({ status })] });
    render(<NodesSection />);
    const card = await screen.findByTestId("node-card");
    expect(card).toHaveAttribute("data-status", status);
    expect(within(card).getByText(label)).toBeInTheDocument();
    expect(within(card).getByText(/Kimlik bilgisi 19 günlük/)).toBeInTheDocument();
  });

  it("says no node is paired yet (never paired)", async () => {
    stubBackend({ nodes: [] });
    render(<NodesSection />);
    expect(await screen.findByText("Henüz eşleştirilmiş Mac düğümü yok.")).toBeInTheDocument();
  });

  it("shows capability availability as text with no input element", async () => {
    stubBackend();
    render(<NodesSection />);
    const list = await screen.findByTestId("node-capabilities");
    expect(within(list).getByText("Kullanılabilir")).toBeInTheDocument();
    expect(within(list).getByText("Mac'te izin verilmedi")).toBeInTheDocument();
    expect(within(list).getByText("Sunucu bu yeteneği tanımıyor")).toBeInTheDocument();
    // No toggle of any kind: policy is local to the Mac (ADR-013 decision 5).
    expect(list.querySelectorAll("input, button, select, textarea, [role=switch], [role=checkbox]")).toHaveLength(0);
  });

  it("shows a node pairing code once and clears it", async () => {
    const fetchMock = stubBackend({
      extra: (url) =>
        url === "/api/nodes/pairing-codes"
          ? jsonResponse(
              { code: "48213906", kind: "pin", device_kind: "node", expires_at: "2026-11-20T11:05:00Z" },
              201,
            )
          : undefined,
    });
    render(<NodesSection />);
    await screen.findByTestId("node-card");
    await userEvent.click(screen.getByRole("button", { name: "Mac eşleştir" }));
    expect(await screen.findByText("48213906")).toBeInTheDocument();
    expect(screen.getByText("Mac eşleştirme PIN'i")).toBeInTheDocument();
    expect(screen.getByTestId("node-pairing-code")).toHaveTextContent("bir daha gösterilmez");
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/nodes/pairing-codes")!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({ kind: "pin" });
    await userEvent.click(screen.getByRole("button", { name: "Tamam" }));
    expect(screen.queryByText("48213906")).not.toBeInTheDocument();
    expect(screen.queryByTestId("node-pairing-code")).not.toBeInTheDocument();
  });

  it("asks for a strong code for another network", async () => {
    const fetchMock = stubBackend({
      extra: (url) =>
        url === "/api/nodes/pairing-codes"
          ? jsonResponse({ code: "strong-code-x", kind: "strong", expires_at: "2026-11-20T11:30:00Z" }, 201)
          : undefined,
    });
    render(<NodesSection />);
    await screen.findByTestId("node-card");
    await userEvent.click(screen.getByRole("button", { name: "Ağ dışı Mac için güçlü kod" }));
    expect(await screen.findByText("strong-code-x")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/nodes/pairing-codes")!;
    expect(JSON.parse(call[1]!.body as string)).toEqual({ kind: "strong" });
  });

  it("revokes only after a confirmation that names the node", async () => {
    const fetchMock = stubBackend({
      extra: (url) =>
        url === `/api/nodes/${ID}/revoke`
          ? jsonResponse({ ...node(), capabilities: undefined, status: "revoked", revoked_at: "2026-11-20T12:00:00Z" })
          : undefined,
    });
    render(<NodesSection />);
    await userEvent.click(await screen.findByRole("button", { name: "İptal et" }));
    const dialog = await findConfirmDialog();
    expect(dialog).toHaveTextContent("Bu Mac düğümü iptal edilsin mi?");
    expect(dialog).toHaveTextContent("“MacBook Pro”");
    await cancelDialog();
    expect(calledWith(fetchMock, "POST", `/api/nodes/${ID}/revoke`)).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "İptal et" }));
    await confirmInDialog("Evet, düğümü iptal et");
    await waitFor(() => expect(screen.getByTestId("node-card")).toHaveAttribute("data-status", "revoked"));
    expect(calledWith(fetchMock, "POST", `/api/nodes/${ID}/revoke`)).toBe(true);
    expect(screen.queryByRole("button", { name: "İptal et" })).not.toBeInTheDocument();
  });

  it("engages the kill switch only after confirmation, then releases it", async () => {
    const fetchMock = stubBackend();
    render(<NodesSection />);
    await userEvent.click(await screen.findByRole("button", { name: "Acil durdurmayı aç" }));
    expect(await findConfirmDialog()).toHaveTextContent("Acil durdurma açılsın mı?");
    expect(calledWith(fetchMock, "POST", "/api/nodes/kill-switch")).toBe(false);
    await confirmInDialog("Evet, aç");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Acil durdurmayı kapat" })).toBeInTheDocument(),
    );
    expect(calledWith(fetchMock, "POST", "/api/nodes/kill-switch")).toBe(true);
    // Pairing is refused while engaged: the buttons are disabled.
    expect(screen.getByRole("button", { name: "Mac eşleştir" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Acil durdurmayı kapat" }));
    expect(await findConfirmDialog()).toHaveTextContent("Acil durdurma kapatılsın mı?");
    expect(calledWith(fetchMock, "DELETE", "/api/nodes/kill-switch")).toBe(false);
    await confirmInDialog("Evet, kapat");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Acil durdurmayı aç" })).toBeInTheDocument(),
    );
    expect(calledWith(fetchMock, "DELETE", "/api/nodes/kill-switch")).toBe(true);
  });

  it("links to the node's invocation log", async () => {
    stubBackend();
    render(<NodesSection />);
    expect(await screen.findByRole("link", { name: "Çağrı kaydı" })).toHaveAttribute(
      "href",
      `/devices/nodes/${ID}`,
    );
  });

  it("reports a failed load", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "x" }, 500)),
    );
    render(<NodesSection />);
    expect(await screen.findByText("Mac düğümleri yüklenemedi.")).toBeInTheDocument();
  });

  it("shows the owner's labels and edits them in a dialog (Phase 13 D3)", async () => {
    const labelled = node({
      room: { id: "r1", name: "Çalışma odası", key: "çalişma odasi" },
      role: "workstation",
      platform: "linux",
      aliases: ["masa"],
    });
    const fetchMock = stubBackend({
      nodes: [labelled],
      extra: (url, init) =>
        url === `/api/nodes/${ID}` && init?.method === "PATCH"
          ? jsonResponse({ ...labelled, name: "Masaüstü" })
          : undefined,
    });
    const user = userEvent.setup();
    render(<NodesSection />);
    const card = await screen.findByTestId("node-card");
    expect(within(card).getByTestId("node-labels")).toHaveTextContent(
      "Çalışma odası · Masaüstü iş istasyonu · Linux · takma adlar: masa",
    );
    await user.click(within(card).getByRole("button", { name: "Düzenle" }));
    const dialog = await screen.findByRole("dialog");
    const name = within(dialog).getByLabelText("Ad");
    await user.clear(name);
    await user.type(name, "Masaüstü");
    await user.click(within(dialog).getByRole("button", { name: "Kaydet" }));
    await waitFor(() => expect(within(card).getByText("Masaüstü")).toBeInTheDocument());
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH")!;
    expect(JSON.parse(patch[1]!.body as string)).toMatchObject({ name: "Masaüstü", room_id: "r1" });
  });
});
