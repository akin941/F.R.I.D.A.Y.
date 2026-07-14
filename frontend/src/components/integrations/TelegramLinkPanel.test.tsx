import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TelegramLinkPanel } from "@/components/integrations/TelegramLinkPanel";

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
          return jsonResponse({ code: "abc123", expires_at: "2026-07-13T00:10:00Z" }, 201);
        }
        if (input.includes("link-status")) {
          return jsonResponse({ linked: false, chat_id: null, linked_at: null });
        }
        return jsonResponse({});
      }),
    );

    render(<TelegramLinkPanel />);

    expect(await screen.findByText("Not linked to a Telegram chat yet.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Link Telegram" }));

    expect(await screen.findByText("Message your Jarvis Telegram bot")).toBeInTheDocument();
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

    render(<TelegramLinkPanel />);

    expect(await screen.findByText(/Linked to chat 999/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Unlink" }));

    await waitFor(() => {
      expect(screen.getByText("Not linked to a Telegram chat yet.")).toBeInTheDocument();
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

    render(<TelegramLinkPanel />);
    await screen.findByText("Not linked to a Telegram chat yet.");

    await userEvent.click(screen.getByRole("combobox", { name: "Telegram workspace" }));
    await userEvent.click(await screen.findByRole("option", { name: "Acme" }));

    expect(await screen.findByText(/Linked to chat 222/)).toBeInTheDocument();
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

    render(<TelegramLinkPanel />);
    await screen.findByText("Not linked to a Telegram chat yet.");

    await userEvent.click(screen.getByRole("button", { name: "Link Telegram" }));
    expect(await screen.findByText("/link abc123")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(3000);

    expect(screen.queryByText("/link abc123")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Link Telegram" })).toBeInTheDocument();
  });
});
