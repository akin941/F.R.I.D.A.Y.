import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryView } from "@/components/memory/MemoryView";
import type { ApiMemory } from "@/types/api";

const { workspaceHolder } = vi.hoisted(() => ({
  workspaceHolder: { current: { id: "w1", name: "Alice", type: "personal", role: "owner" } } as {
    current: { id: string; name: string; type: "personal" | "company"; role: string };
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspaceHolder.current }),
}));

function memory(overrides: Partial<ApiMemory> = {}): ApiMemory {
  return {
    id: "m1",
    workspace: "w1",
    memory_type: "preference",
    sensitivity: "normal",
    status: "approved",
    source: "user_created",
    title: "Coffee",
    content: "Drinks coffee black.",
    created_by: "u1",
    source_conversation: null,
    created_at: "2026-07-06T00:00:00Z",
    updated_at: "2026-07-06T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("MemoryView", () => {
  beforeEach(() => {
    workspaceHolder.current = { id: "w1", name: "Alice", type: "personal", role: "owner" };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads and renders the pending inbox and the memory list", async () => {
    const pending = memory({ id: "p1", status: "pending_approval", source: "ai_suggested" });
    const approved = memory({ id: "a1", title: "Tea" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([pending, approved])),
    );

    render(<MemoryView />);

    expect(await screen.findByText("Pending approval (1)")).toBeInTheDocument();
    expect(screen.getByText("Coffee")).toBeInTheDocument();
    expect(screen.getByText("Tea")).toBeInTheDocument();
  });

  it("approves a pending memory and moves it out of the inbox", async () => {
    const pending = memory({ id: "p1", status: "pending_approval", source: "ai_suggested" });
    const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
      async (input) => {
        if (input.includes("/approve")) {
          return jsonResponse({ ...pending, status: "approved" });
        }
        return jsonResponse([pending]);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryView />);
    await screen.findByText("Coffee");

    const approveButton = screen.getByRole("button", { name: "Approve" });
    await userEvent.click(approveButton);

    await waitFor(() => {
      expect(screen.getByText("Pending approval")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    });
    const [url, options] = fetchMock.mock.calls.find(([u]) =>
      String(u).includes("/approve"),
    ) as [string, RequestInit];
    expect(url).toBe("/api/memories/p1/approve");
    expect(options.method).toBe("POST");
  });

  it("rejects a pending memory", async () => {
    const pending = memory({ id: "p1", status: "pending_approval", source: "ai_suggested" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.includes("/reject")) {
          return jsonResponse({ ...pending, status: "rejected" });
        }
        return jsonResponse([pending]);
      }),
    );

    render(<MemoryView />);
    await screen.findByText("Coffee");
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() => {
      expect(screen.getByText("Nothing waiting for review.")).toBeInTheDocument();
    });
  });

  it("creates a new memory and shows it in the list", async () => {
    const created = memory({ id: "new1", title: "New fact", content: "A fact." });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/memories") {
          return jsonResponse(created, 201);
        }
        return jsonResponse([]);
      }),
    );

    render(<MemoryView />);
    await screen.findByText("Nothing waiting for review.");

    await userEvent.click(screen.getByRole("button", { name: "New memory" }));
    await userEvent.type(screen.getByLabelText("Title"), "New fact");
    await userEvent.type(screen.getByLabelText("Content"), "A fact.");
    await userEvent.click(screen.getByRole("button", { name: "Create memory" }));

    expect(await screen.findByText("New fact")).toBeInTheDocument();
  });

  it("edits an approved memory", async () => {
    const approved = memory();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "PATCH") {
          return jsonResponse({ ...approved, title: "Coffee (edited)" });
        }
        return jsonResponse([approved]);
      }),
    );

    render(<MemoryView />);
    await screen.findByText("Coffee");
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));

    const titleInput = screen.getByLabelText("Title") as HTMLInputElement;
    await userEvent.clear(titleInput);
    await userEvent.type(titleInput, "Coffee (edited)");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Coffee (edited)")).toBeInTheDocument();
  });

  it("forgets an approved memory", async () => {
    const approved = memory();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.includes("/forget")) {
          return jsonResponse({ ...approved, status: "archived" });
        }
        return jsonResponse([approved]);
      }),
    );

    render(<MemoryView />);
    await screen.findByText("Coffee");
    await userEvent.click(screen.getByRole("button", { name: "Forget" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Restore" })).toBeInTheDocument();
    });
  });

  it("restores an archived memory", async () => {
    const archived = memory({ status: "archived" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.includes("/restore")) {
          return jsonResponse({ ...archived, status: "approved" });
        }
        return jsonResponse([archived]);
      }),
    );

    render(<MemoryView />);
    await screen.findByText("Coffee");
    await userEvent.click(screen.getByRole("button", { name: "Restore" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Forget" })).toBeInTheDocument();
    });
  });

  it("hard-deletes a rejected memory", async () => {
    const rejected = memory({ status: "rejected" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        return jsonResponse([rejected]);
      }),
    );

    render(<MemoryView />);
    await screen.findByText("Coffee");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(screen.queryByText("Coffee")).not.toBeInTheDocument();
    });
  });

  it("opens the export endpoint for the active workspace", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );
    const openMock = vi.fn();
    vi.stubGlobal("open", openMock);

    render(<MemoryView />);
    await screen.findByText("Nothing waiting for review.");
    await userEvent.click(screen.getByRole("button", { name: "Export" }));

    expect(openMock).toHaveBeenCalledWith("/api/memories/export?workspace=w1", "_blank");
  });

  it("creates a vault export approval request and shows the plain-language success notice", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/approvals/requests") {
          return jsonResponse({ id: "req1", tool_name: "vault_export", status: "pending" }, 201);
        }
        return jsonResponse([]);
      }),
    );

    render(<MemoryView />);
    await screen.findByText("Nothing waiting for review.");
    await userEvent.click(screen.getByRole("button", { name: "Export to Obsidian vault" }));

    expect(
      await screen.findByText(
        "Request created. It needs approval from another workspace admin before it runs.",
      ),
    ).toBeInTheDocument();
  });

  it("shows an error notice when the vault export request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/approvals/requests") {
          return jsonResponse({ detail: "OBSIDIAN_VAULT_PATH is not configured." }, 400);
        }
        return jsonResponse([]);
      }),
    );

    render(<MemoryView />);
    await screen.findByText("Nothing waiting for review.");
    await userEvent.click(screen.getByRole("button", { name: "Export to Obsidian vault" }));

    expect(await screen.findByText("OBSIDIAN_VAULT_PATH is not configured.")).toBeInTheDocument();
  });

  it("disables the vault export button while a request is in flight, to avoid duplicate submissions", async () => {
    let resolvePost: ((response: Response) => void) | undefined;
    const postPending = new Promise<Response>((resolve) => {
      resolvePost = resolve;
    });
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "POST" && input === "/api/approvals/requests") {
        return postPending;
      }
      return jsonResponse([]);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryView />);
    await screen.findByText("Nothing waiting for review.");
    const button = screen.getByRole("button", { name: "Export to Obsidian vault" });
    await userEvent.click(button);
    // A second click while the first POST is still in flight (Codex review,
    // branch 4: double-clicking created duplicate approval requests).
    await userEvent.click(button);

    expect(button).toBeDisabled();
    resolvePost?.(jsonResponse({ id: "req1", tool_name: "vault_export", status: "pending" }, 201));
    await screen.findByText(
      "Request created. It needs approval from another workspace admin before it runs.",
    );

    const postCalls = fetchMock.mock.calls.filter(
      ([, init]) => init?.method === "POST" && init?.body,
    );
    expect(postCalls).toHaveLength(1);
  });

  it("discards a vault export response for a workspace the user has already switched away from", async () => {
    let resolvePost: ((response: Response) => void) | undefined;
    const postPending = new Promise<Response>((resolve) => {
      resolvePost = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/approvals/requests") {
          return postPending;
        }
        return jsonResponse([]);
      }),
    );

    const { rerender } = render(<MemoryView />);
    await screen.findByText("Nothing waiting for review.");
    await userEvent.click(screen.getByRole("button", { name: "Export to Obsidian vault" }));

    // The user switches away before workspace A's POST resolves.
    workspaceHolder.current = { id: "w2", name: "Acme", type: "company", role: "member" };
    rerender(<MemoryView />);

    resolvePost?.(jsonResponse({ id: "req1", tool_name: "vault_export", status: "pending" }, 201));
    await new Promise((resolve) => setTimeout(resolve, 10));

    // Workspace A's success notice must not appear on workspace B, and B's
    // button must not be left disabled by A's now-irrelevant request.
    expect(
      screen.queryByText(
        "Request created. It needs approval from another workspace admin before it runs.",
      ),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export to Obsidian vault" })).not.toBeDisabled();
  });

  it("closes an in-progress create draft on a workspace switch instead of letting it cross into the new workspace", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );

    const { rerender } = render(<MemoryView />);
    await screen.findByText("Nothing waiting for review.");

    await userEvent.click(screen.getByRole("button", { name: "New memory" }));
    await userEvent.type(screen.getByLabelText("Title"), "Workspace A draft");
    expect(screen.getByLabelText("Title")).toHaveValue("Workspace A draft");

    workspaceHolder.current = { id: "w2", name: "Acme", type: "company", role: "member" };
    rerender(<MemoryView />);

    // The draft form must not survive the switch: neither its fields nor
    // the "creating" state should carry over into workspace w2.
    expect(screen.queryByLabelText("Title")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New memory" })).toBeInTheDocument();
  });

  it("discards a create response for a workspace the user has already switched away from", async () => {
    let resolveCreate: ((response: Response) => void) | undefined;
    const createPending = new Promise<Response>((resolve) => {
      resolveCreate = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/memories") {
          return createPending;
        }
        return jsonResponse([]);
      }),
    );

    const { rerender } = render(<MemoryView />);
    await screen.findByText("Nothing waiting for review.");
    await userEvent.click(screen.getByRole("button", { name: "New memory" }));
    await userEvent.type(screen.getByLabelText("Title"), "Workspace A draft");
    await userEvent.type(screen.getByLabelText("Content"), "Some content.");
    await userEvent.click(screen.getByRole("button", { name: "Create memory" }));

    // The user switches away before workspace A's create call resolves.
    workspaceHolder.current = { id: "w2", name: "Acme", type: "company", role: "member" };
    rerender(<MemoryView />);

    resolveCreate?.(jsonResponse(memory({ id: "late1", workspace: "w1", title: "Workspace A draft" }), 201));
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(screen.queryByText("Workspace A draft")).not.toBeInTheDocument();
  });
});
