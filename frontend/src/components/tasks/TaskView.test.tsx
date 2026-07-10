import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TaskView } from "@/components/tasks/TaskView";
import type { ApiTask } from "@/types/api";

const { workspaceHolder } = vi.hoisted(() => ({
  workspaceHolder: { current: { id: "w1", name: "Alice", type: "personal", role: "owner" } } as {
    current: { id: string; name: string; type: "personal" | "company"; role: string };
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspaceHolder.current }),
}));

function task(overrides: Partial<ApiTask> = {}): ApiTask {
  return {
    id: "t1",
    workspace: "w1",
    title: "Buy milk",
    description: "",
    status: "open",
    priority: "normal",
    due_date: null,
    source: "user_created",
    created_by: "u1",
    created_at: "2026-07-08T00:00:00Z",
    updated_at: "2026-07-08T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("TaskView", () => {
  beforeEach(() => {
    workspaceHolder.current = { id: "w1", name: "Alice", type: "personal", role: "owner" };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads and renders the task list", async () => {
    const row = task({ title: "Buy milk" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([row])),
    );

    render(<TaskView />);

    expect(await screen.findByText("Buy milk")).toBeInTheDocument();
  });

  it("creates a new task and shows it in the list", async () => {
    const created = task({ id: "new1", title: "Water plants" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/tasks") {
          return jsonResponse(created, 201);
        }
        return jsonResponse([]);
      }),
    );

    render(<TaskView />);
    await screen.findByText("No tasks match these filters.");

    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    await userEvent.type(screen.getByLabelText("Title"), "Water plants");
    await userEvent.click(screen.getByRole("button", { name: "Create task" }));

    expect(await screen.findByText("Water plants")).toBeInTheDocument();
  });

  it("completes an open task", async () => {
    const row = task();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.includes("/complete")) {
          return jsonResponse({ ...row, status: "done" });
        }
        return jsonResponse([row]);
      }),
    );

    render(<TaskView />);
    await screen.findByText("Buy milk");
    await userEvent.click(screen.getByRole("button", { name: "Complete" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Reopen" })).toBeInTheDocument();
    });
  });

  it("cancels an open task", async () => {
    const row = task();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.includes("/cancel")) {
          return jsonResponse({ ...row, status: "cancelled" });
        }
        return jsonResponse([row]);
      }),
    );

    render(<TaskView />);
    await screen.findByText("Buy milk");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Reopen" })).toBeInTheDocument();
    });
  });

  it("reopens a done task", async () => {
    const row = task({ status: "done" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.includes("/reopen")) {
          return jsonResponse({ ...row, status: "open" });
        }
        return jsonResponse([row]);
      }),
    );

    render(<TaskView />);
    await screen.findByText("Buy milk");
    await userEvent.click(screen.getByRole("button", { name: "Reopen" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Complete" })).toBeInTheDocument();
    });
  });

  it("edits a task", async () => {
    const row = task();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "PATCH") {
          return jsonResponse({ ...row, title: "Buy oat milk" });
        }
        return jsonResponse([row]);
      }),
    );

    render(<TaskView />);
    await screen.findByText("Buy milk");
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));

    const titleInput = screen.getByLabelText("Title") as HTMLInputElement;
    await userEvent.clear(titleInput);
    await userEvent.type(titleInput, "Buy oat milk");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Buy oat milk")).toBeInTheDocument();
  });

  it("deletes a task", async () => {
    const row = task();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        return jsonResponse([row]);
      }),
    );

    render(<TaskView />);
    await screen.findByText("Buy milk");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(screen.queryByText("Buy milk")).not.toBeInTheDocument();
    });
  });

  it("filters by status", async () => {
    const openRow = task({ id: "o1", title: "Open one", status: "open" });
    const doneRow = task({ id: "d1", title: "Done one", status: "done" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([openRow, doneRow])),
    );

    render(<TaskView />);
    await screen.findByText("Open one");
    expect(screen.getByText("Done one")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("combobox", { name: "Status filter" }));
    await userEvent.click(await screen.findByRole("option", { name: "Done" }));

    await waitFor(() => {
      expect(screen.queryByText("Open one")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Done one")).toBeInTheDocument();
  });

  it("closes an in-progress create draft on a workspace switch instead of letting it cross into the new workspace", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );

    const { rerender } = render(<TaskView />);
    await screen.findByText("No tasks match these filters.");

    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    await userEvent.type(screen.getByLabelText("Title"), "Workspace A draft");
    expect(screen.getByLabelText("Title")).toHaveValue("Workspace A draft");

    workspaceHolder.current = { id: "w2", name: "Acme", type: "company", role: "member" };
    rerender(<TaskView />);

    expect(screen.queryByLabelText("Title")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New task" })).toBeInTheDocument();
  });

  it("discards a create response for a workspace the user has already switched away from", async () => {
    let resolveCreate: ((response: Response) => void) | undefined;
    const createPending = new Promise<Response>((resolve) => {
      resolveCreate = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/tasks") {
          return createPending;
        }
        return jsonResponse([]);
      }),
    );

    const { rerender } = render(<TaskView />);
    await screen.findByText("No tasks match these filters.");
    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    await userEvent.type(screen.getByLabelText("Title"), "Workspace A draft");
    await userEvent.click(screen.getByRole("button", { name: "Create task" }));

    workspaceHolder.current = { id: "w2", name: "Acme", type: "company", role: "member" };
    rerender(<TaskView />);

    resolveCreate?.(jsonResponse(task({ id: "late1", workspace: "w1", title: "Workspace A draft" }), 201));
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(screen.queryByText("Workspace A draft")).not.toBeInTheDocument();
  });
});
