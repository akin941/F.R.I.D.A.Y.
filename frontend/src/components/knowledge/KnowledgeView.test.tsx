import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KnowledgeView } from "@/components/knowledge/KnowledgeView";
import type { ApiKnowledgeDocument } from "@/types/api";

const { workspaceHolder } = vi.hoisted(() => ({
  workspaceHolder: { current: { id: "w1", name: "Alice", type: "personal", role: "owner" } } as {
    current: { id: string; name: string; type: "personal" | "company"; role: string };
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspaceHolder.current }),
}));

function document(overrides: Partial<ApiKnowledgeDocument> = {}): ApiKnowledgeDocument {
  return {
    id: "d1",
    workspace: "w1",
    source_type: "pasted_text",
    title: "Lantern mode",
    status: "ready",
    sensitivity: "normal",
    mime_type: "text/plain",
    byte_size: 42,
    source_url: "",
    error_message: "",
    created_by: "u1",
    created_at: "2026-07-08T00:00:00Z",
    updated_at: "2026-07-08T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("KnowledgeView", () => {
  beforeEach(() => {
    workspaceHolder.current = { id: "w1", name: "Alice", type: "personal", role: "owner" };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads and renders the document list", async () => {
    const ready = document({ id: "d1", title: "Lantern mode" });
    const failed = document({ id: "d2", title: "Broken upload", status: "failed", error_message: "Bad file." });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([ready, failed])),
    );

    render(<KnowledgeView />);

    expect(await screen.findByText("Lantern mode")).toBeInTheDocument();
    expect(screen.getByText("Broken upload")).toBeInTheDocument();
    expect(screen.getByText("Bad file.")).toBeInTheDocument();
  });

  it("creates a pasted-text document and shows it in the list", async () => {
    const created = document({ id: "new1", title: "New doc", status: "pending" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/knowledge/documents/pasted-text") {
          return jsonResponse(created, 201);
        }
        return jsonResponse([]);
      }),
    );

    render(<KnowledgeView />);
    await screen.findByText("No documents match these filters.");

    await userEvent.click(screen.getByRole("button", { name: "New document" }));
    await userEvent.type(screen.getByLabelText("Title"), "New doc");
    await userEvent.type(screen.getByLabelText("Content"), "Some content.");
    await userEvent.click(screen.getByRole("button", { name: "Create document" }));

    expect(await screen.findByText("New doc")).toBeInTheDocument();
  });

  it("creates a URL document without a content field", async () => {
    const created = document({ id: "new2", title: "Article", source_type: "url", status: "pending" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST" && input === "/api/knowledge/documents/url") {
          return jsonResponse(created, 201);
        }
        return jsonResponse([]);
      }),
    );

    render(<KnowledgeView />);
    await screen.findByText("No documents match these filters.");

    await userEvent.click(screen.getByRole("button", { name: "New document" }));
    await userEvent.click(screen.getByLabelText("Source"));
    await userEvent.click(await screen.findByRole("option", { name: "From URL" }));
    await userEvent.type(screen.getByLabelText("Title"), "Article");
    await userEvent.type(screen.getByLabelText("URL"), "https://example.com/a");
    await userEvent.click(screen.getByRole("button", { name: "Create document" }));

    expect(await screen.findByText("Article")).toBeInTheDocument();
  });

  it("surfaces a field-keyed validation error from the backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "POST") {
          return jsonResponse({ content: ["content must not be empty."] }, 400);
        }
        return jsonResponse([]);
      }),
    );

    render(<KnowledgeView />);
    await screen.findByText("No documents match these filters.");

    await userEvent.click(screen.getByRole("button", { name: "New document" }));
    await userEvent.type(screen.getByLabelText("Title"), "Empty doc");
    await userEvent.type(screen.getByLabelText("Content"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Create document" }));

    expect(await screen.findByText("content must not be empty.")).toBeInTheDocument();
  });

  it("deletes a document", async () => {
    const ready = document();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        return jsonResponse([ready]);
      }),
    );

    render(<KnowledgeView />);
    await screen.findByText("Lantern mode");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(screen.queryByText("Lantern mode")).not.toBeInTheDocument();
    });
  });

  it("filters the list by status", async () => {
    const ready = document({ id: "d1", title: "Ready doc", status: "ready" });
    const failed = document({ id: "d2", title: "Failed doc", status: "failed" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([ready, failed])),
    );

    render(<KnowledgeView />);
    await screen.findByText("Ready doc");

    await userEvent.click(screen.getByLabelText("Status filter"));
    await userEvent.click(await screen.findByRole("option", { name: "Failed" }));

    expect(screen.queryByText("Ready doc")).not.toBeInTheDocument();
    expect(screen.getByText("Failed doc")).toBeInTheDocument();
  });

  it("closes an in-progress create draft on a workspace switch instead of letting it cross into the new workspace", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([])),
    );

    const { rerender } = render(<KnowledgeView />);
    await screen.findByText("No documents match these filters.");

    await userEvent.click(screen.getByRole("button", { name: "New document" }));
    await userEvent.type(screen.getByLabelText("Title"), "Workspace A draft");
    expect(screen.getByLabelText("Title")).toHaveValue("Workspace A draft");

    workspaceHolder.current = { id: "w2", name: "Acme", type: "company", role: "member" };
    rerender(<KnowledgeView />);

    expect(screen.queryByLabelText("Title")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New document" })).toBeInTheDocument();
  });
});
