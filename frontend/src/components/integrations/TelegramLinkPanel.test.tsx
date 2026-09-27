import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TelegramLinkPanel } from "@/components/integrations/TelegramLinkPanel";
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

// A code is only shown while it is still valid.
function inTenMinutes() {
  return new Date(Date.now() + 10 * 60_000).toISOString();
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("TelegramLinkPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("shows the not-linked state and requests a link code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST") {
          return jsonResponse({ code: "abc123", expires_at: inTenMinutes() }, 201);
        }
        if (input.includes("link-status")) {
          return jsonResponse({ linked: false, chat_id: null, linked_at: null });
        }
        return jsonResponse({});
      }),
    );

    renderWithClient(<TelegramLinkPanel />);

    expect(await screen.findByText("Henüz bir Telegram sohbetine bağlı değil.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Telegram'ı bağla" }));

    expect(await screen.findByText("Jarvis Telegram botuna yaz")).toBeInTheDocument();
    expect(screen.getByText("/link abc123")).toBeInTheDocument();
  });

  it("shows the linked state and unlinks", async () => {
    let linked = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          linked = false;
          return new Response(null, { status: 204 });
        }
        if (input.includes("link-status")) {
          return jsonResponse(
            linked
              ? { linked: true, chat_id: "999", linked_at: "2026-07-13T00:00:00Z" }
              : { linked: false, chat_id: null, linked_at: null },
          );
        }
        return jsonResponse({});
      }),
    );

    renderWithClient(<TelegramLinkPanel />);

    expect(await screen.findByText(/Şu sohbete bağlı: 999/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Bağlantıyı kaldır" }));

    await waitFor(() => {
      expect(screen.getByText("Henüz bir Telegram sohbetine bağlı değil.")).toBeInTheDocument();
    });
  });

  it("switches workspace via the picker and reloads status for the new one", async () => {
    const fetchMock = vi.fn(async (input: string) => {
      if (input.includes("workspace=w2")) {
        return jsonResponse({ linked: true, chat_id: "222", linked_at: "2026-07-13T00:00:00Z" });
      }
      return jsonResponse({ linked: false, chat_id: null, linked_at: null });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<TelegramLinkPanel />);
    await screen.findByText("Henüz bir Telegram sohbetine bağlı değil.");

    await userEvent.click(screen.getByRole("combobox", { name: "Telegram çalışma alanı" }));
    await userEvent.click(await screen.findByRole("option", { name: "Acme" }));

    expect(await screen.findByText(/Şu sohbete bağlı: 222/)).toBeInTheDocument();
  });

  it("does not get stuck loading if Unlink's reload arrives after the workspace was switched away", async () => {
    let resolveDelete: (response: Response) => void = () => {};
    const deletePromise = new Promise<Response>((resolve) => {
      resolveDelete = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "DELETE") return deletePromise;
        if (input.includes("workspace=w2")) {
          return jsonResponse({ linked: true, chat_id: "333", linked_at: "2026-07-13T00:00:00Z" });
        }
        return jsonResponse({ linked: true, chat_id: "999", linked_at: "2026-07-13T00:00:00Z" });
      }),
    );

    renderWithClient(<TelegramLinkPanel />);
    await screen.findByText(/Şu sohbete bağlı: 999/);

    await userEvent.click(screen.getByRole("button", { name: "Bağlantıyı kaldır" }));

    // Switch workspace while the DELETE for w1 is still in flight.
    await userEvent.click(screen.getByRole("combobox", { name: "Telegram çalışma alanı" }));
    await userEvent.click(await screen.findByRole("option", { name: "Acme" }));
    expect(await screen.findByText(/Şu sohbete bağlı: 333/)).toBeInTheDocument();

    // The stale w1 unlink now resolves -- proves the round-3 P2 fix: its
    // follow-up reload must not resurrect the loading skeleton for w2,
    // which already finished loading its own (unrelated) status.
    resolveDelete(new Response(null, { status: 204 }));
    await waitFor(() => {
      expect(screen.getByText(/Şu sohbete bağlı: 333/)).toBeInTheDocument();
    });
  });

  it("discards a link-code response that arrives after the workspace was switched away", async () => {
    let resolvePost: (response: Response) => void = () => {};
    const postPromise = new Promise<Response>((resolve) => {
      resolvePost = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST") return postPromise;
        if (input.includes("workspace=w2")) {
          return jsonResponse({ linked: true, chat_id: "222", linked_at: "2026-07-13T00:00:00Z" });
        }
        return jsonResponse({ linked: false, chat_id: null, linked_at: null });
      }),
    );

    renderWithClient(<TelegramLinkPanel />);
    await screen.findByText("Henüz bir Telegram sohbetine bağlı değil.");

    await userEvent.click(screen.getByRole("button", { name: "Telegram'ı bağla" }));
    expect(screen.getByRole("button", { name: "İsteniyor…" })).toBeDisabled();

    // Switch workspace while the request for w1 is still in flight.
    await userEvent.click(screen.getByRole("combobox", { name: "Telegram çalışma alanı" }));
    await userEvent.click(await screen.findByRole("option", { name: "Acme" }));
    expect(await screen.findByText(/Şu sohbete bağlı: 222/)).toBeInTheDocument();

    // The stale w1 response now resolves -- proves the round-2 P2 fix: it
    // must not surface w1's code while the panel shows w2, and it must not
    // leave the (workspace-agnostic) requesting flag stuck.
    resolvePost(jsonResponse({ code: "abc123", expires_at: inTenMinutes() }, 201));
    await waitFor(() => {
      expect(screen.queryByText("/link abc123")).not.toBeInTheDocument();
    });
    expect(screen.getByText(/Şu sohbete bağlı: 222/)).toBeInTheDocument();
  });

  it("clears an expired code once its countdown reaches zero", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-07-13T00:00:00Z"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST") {
          return jsonResponse({ code: "abc123", expires_at: "2026-07-13T00:00:02Z" }, 201);
        }
        if (input.includes("link-status")) {
          return jsonResponse({ linked: false, chat_id: null, linked_at: null });
        }
        return jsonResponse({});
      }),
    );

    renderWithClient(<TelegramLinkPanel />);
    await screen.findByText("Henüz bir Telegram sohbetine bağlı değil.");

    await userEvent.click(screen.getByRole("button", { name: "Telegram'ı bağla" }));
    expect(await screen.findByText("/link abc123")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(3000);

    expect(screen.queryByText("/link abc123")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Telegram'ı bağla" })).toBeInTheDocument();
  });
});
