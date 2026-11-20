import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DevicesPanel } from "@/components/integrations/DevicesPanel";
import { calledWith, cancelDialog, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import type { ApiTrustedDevice } from "@/types/api";

function device(overrides: Partial<ApiTrustedDevice>): ApiTrustedDevice {
  return {
    id: "d1",
    name: "Mac",
    kind: "browser",
    enrolled_via: "pin",
    enrolled_ip: "192.168.1.5",
    user_agent: "",
    created_at: "2026-09-01T10:00:00Z",
    last_seen_at: "2026-09-27T10:00:00Z",
    last_seen_ip: "192.168.1.5",
    revoked_at: null,
    revoke_reason: "",
    idle_expires_at: "2026-12-26T10:00:00Z",
    credential_expires_at: null,
    secret_issued_at: null,
    is_current: false,
    ...overrides,
  };
}

const DEVICES = [
  device({ id: "11111111-1111-4111-8111-111111111111", name: "Mac", is_current: true }),
  device({ id: "22222222-2222-4222-8222-222222222222", name: "Telefon", enrolled_via: "remote_key" }),
];

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function stubBackend(extra: (url: string, init?: RequestInit) => Response | undefined = () => undefined) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const handled = extra(url, init);
    if (handled) return handled;
    if (url === "/api/auth/devices") return jsonResponse(DEVICES);
    if (url === "/api/auth/remote-key") {
      return jsonResponse({ active: false, created_at: null, last_used_at: null });
    }
    throw new Error(`Unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("DevicesPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists devices and marks the current one", async () => {
    stubBackend();
    render(<DevicesPanel />);
    expect(await screen.findByText("Telefon")).toBeInTheDocument();
    expect(screen.getByText("Bu cihaz")).toBeInTheDocument();
    expect(screen.getByText("Uzaktan erişim anahtarı ile")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Bu cihazı unut" })).toBeInTheDocument();
  });

  it("revokes another device only after confirmation", async () => {
    const fetchMock = stubBackend((url) =>
      url.endsWith("/revoke") && url.includes("2222")
        ? jsonResponse({ ...DEVICES[1], revoked_at: "2026-09-27T11:00:00Z" })
        : undefined,
    );
    render(<DevicesPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "İptal et" }));
    expect(await findConfirmDialog()).toHaveTextContent(`“${DEVICES[1]!.name}”`);
    expect(calledWith(fetchMock, "POST", `/api/auth/devices/${DEVICES[1]!.id}/revoke`)).toBe(
      false,
    );
    await confirmInDialog("Evet, iptal et");
    await waitFor(() => expect(screen.getByText(/İptal edildi/)).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/auth/devices/${DEVICES[1]!.id}/revoke`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it('"Yeni cihaz eşleştir" shows a one-time PIN', async () => {
    stubBackend((url) =>
      url === "/api/auth/pairing-codes"
        ? jsonResponse({ code: "48213906", kind: "pin", expires_at: "2026-09-27T11:05:00Z" }, 201)
        : undefined,
    );
    render(<DevicesPanel />);
    await screen.findByText("Telefon");
    await userEvent.click(screen.getByRole("button", { name: "Yeni cihaz eşleştir" }));
    expect(await screen.findByText("Eşleştirme PIN'i")).toBeInTheDocument();
    expect(screen.getByText("48213906")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Tamam" }));
    expect(screen.queryByText("48213906")).not.toBeInTheDocument();
  });

  it('"Bu cihazı unut" does nothing until confirmed', async () => {
    const fetchMock = stubBackend();
    render(<DevicesPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Bu cihazı unut" }));
    expect(await findConfirmDialog()).toHaveTextContent("Bu cihaz unutulsun mu?");
    await cancelDialog();
    expect(calledWith(fetchMock, "POST", "/api/auth/forget")).toBe(false);
  });

  it("creates a remote key only after confirmation, then shows it once", async () => {
    const fetchMock = stubBackend((url, init) =>
      url === "/api/auth/remote-key" && init?.method === "POST"
        ? jsonResponse({
            active: true,
            created_at: "2026-09-27T11:00:00Z",
            last_used_at: null,
            key: "jrk_secret",
          })
        : undefined,
    );
    render(<DevicesPanel />);
    await screen.findByText("Telefon");
    await userEvent.click(screen.getByRole("button", { name: "Anahtar oluştur" }));
    expect(await findConfirmDialog()).toHaveTextContent("Uzaktan erişim anahtarı oluşturulsun mu?");
    expect(calledWith(fetchMock, "POST", "/api/auth/remote-key")).toBe(false);
    await confirmInDialog("Evet, oluştur");
    expect(await screen.findByText("jrk_secret")).toBeInTheDocument();
  });

  it("closes the remote key only after confirmation", async () => {
    const fetchMock = stubBackend((url, init) => {
      if (url === "/api/auth/remote-key/revoke") {
        return jsonResponse({ active: false, created_at: null, last_used_at: null });
      }
      if (url === "/api/auth/remote-key" && init?.method === "GET") {
        return jsonResponse({
          active: true,
          created_at: "2026-09-27T11:00:00Z",
          last_used_at: null,
        });
      }
      return undefined;
    });
    render(<DevicesPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Kapat" }));
    expect(calledWith(fetchMock, "POST", "/api/auth/remote-key/revoke")).toBe(false);
    await confirmInDialog("Evet, kapat");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Anahtar oluştur" })).toBeInTheDocument(),
    );
    expect(calledWith(fetchMock, "POST", "/api/auth/remote-key/revoke")).toBe(true);
  });

  it("badges a Friday Node and uses node wording to revoke it", async () => {
    const node = device({
      id: "33333333-3333-4333-8333-333333333333",
      name: "MacBook Pro",
      kind: "node",
      enrolled_via: "strong_code",
      credential_expires_at: "2027-02-18T10:00:00Z",
      secret_issued_at: "2026-11-20T10:00:00Z",
    });
    const fetchMock = stubBackend((url) => {
      if (url === "/api/auth/devices") return jsonResponse([DEVICES[0], node]);
      if (url.endsWith("/revoke") && url.includes("3333")) {
        return jsonResponse({ ...node, revoked_at: "2026-11-20T11:00:00Z" });
      }
      return undefined;
    });
    render(<DevicesPanel />);
    expect(await screen.findByText("MacBook Pro")).toBeInTheDocument();
    // Exactly one badge: the browser rows carry none.
    expect(screen.getAllByText("Mac düğümü")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "İptal et" }));
    const dialog = await findConfirmDialog();
    expect(dialog).toHaveTextContent("Bu Mac düğümü iptal edilsin mi?");
    expect(dialog).toHaveTextContent("“MacBook Pro” düğümünün bağlantısı hemen kesilir");
    expect(calledWith(fetchMock, "POST", `/api/auth/devices/${node.id}/revoke`)).toBe(false);
    await confirmInDialog("Evet, düğümü iptal et");
    await waitFor(() => expect(screen.getByText(/İptal edildi/)).toBeInTheDocument());
    expect(calledWith(fetchMock, "POST", `/api/auth/devices/${node.id}/revoke`)).toBe(true);
  });

  it("shows no badge for browsers", async () => {
    stubBackend();
    render(<DevicesPanel />);
    expect(await screen.findByText("Telefon")).toBeInTheDocument();
    expect(screen.queryByText("Mac düğümü")).not.toBeInTheDocument();
  });
});
