import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TrashView } from "@/components/trash/TrashView";
import { calledWith, cancelDialog, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import type { ApiKnowledgeDocument, ApiTask } from "@/types/api";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({
    activeWorkspace: { id: "w1", name: "Alice", type: "personal", role: "owner" },
    trashRetentionDays: 10,
  }),
}));

const TASK_ID = "11111111-1111-1111-1111-111111111111";
const DOC_ID = "22222222-2222-2222-2222-222222222222";

function task(overrides: Partial<ApiTask> = {}): ApiTask {
  return {
    id: TASK_ID,
    workspace: "w1",
    title: "Süt al",
    description: "",
    status: "open",
    priority: "normal",
    due_date: null,
    source: "manual",
    created_by: "u1",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-20T00:00:00Z",
    deleted_at: "2026-09-20T12:00:00Z",
    ...overrides,
  } as ApiTask;
}

function document(overrides: Partial<ApiKnowledgeDocument> = {}): ApiKnowledgeDocument {
  return {
    id: DOC_ID,
    workspace: "w1",
    source_type: "pasted_text",
    title: "Toplantı notu",
    status: "ready",
    sensitivity: "normal",
    mime_type: "text/plain",
    byte_size: 10,
    source_url: "",
    error_message: "",
    created_by: "u1",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-21T00:00:00Z",
    deleted_at: "2026-09-21T00:00:00Z",
    ...overrides,
  } as ApiKnowledgeDocument;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function stub(
  tasks: ApiTask[],
  documents: ApiKnowledgeDocument[],
  extra: (url: string, init?: RequestInit) => Response | undefined = () => undefined,
) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const handled = extra(url, init);
    if (handled) return handled;
    if (url === "/api/tasks/trash?workspace=w1") return jsonResponse(tasks);
    if (url === "/api/knowledge/documents/trash?workspace=w1") return jsonResponse(documents);
    throw new Error(`Unexpected fetch ${init?.method ?? "GET"} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("TrashView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists trashed tasks and documents with their purge date and the retention window", async () => {
    stub([task()], [document()]);

    renderWithClient(<TrashView />);

    expect(await screen.findByText("Süt al")).toBeInTheDocument();
    expect(screen.getByText("Toplantı notu")).toBeInTheDocument();
    expect(screen.getByText(/10 gün burada kalır/)).toBeInTheDocument();
    const row = screen.getByTestId(`trash-task-${TASK_ID}`);
    // deleted_at + 10 days.
    expect(within(row).getByText(/Kalıcı silinme: 30 Eyl 2026/)).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    stub([], []);
    renderWithClient(<TrashView />);
    expect(await screen.findByText("Çöp kutusu boş.")).toBeInTheDocument();
  });

  it("restores a task and a document without confirmation (reversible)", async () => {
    const fetchMock = stub([task()], [document()], (url) => {
      if (url === `/api/tasks/${TASK_ID}/restore`) return jsonResponse(task({ deleted_at: null }));
      if (url === `/api/knowledge/documents/${DOC_ID}/restore`) {
        return jsonResponse(document({ deleted_at: null }));
      }
      return undefined;
    });

    renderWithClient(<TrashView />);
    const taskRow = await screen.findByTestId(`trash-task-${TASK_ID}`);
    await userEvent.click(within(taskRow).getByRole("button", { name: "Geri yükle" }));
    await waitFor(() => expect(screen.queryByText("Süt al")).not.toBeInTheDocument());

    const docRow = screen.getByTestId(`trash-document-${DOC_ID}`);
    await userEvent.click(within(docRow).getByRole("button", { name: "Geri yükle" }));
    await waitFor(() => expect(screen.getByText("Çöp kutusu boş.")).toBeInTheDocument());
    expect(calledWith(fetchMock, "POST", `/api/tasks/${TASK_ID}/restore`)).toBe(true);
    expect(calledWith(fetchMock, "POST", `/api/knowledge/documents/${DOC_ID}/restore`)).toBe(true);
  });

  it("reports a refused restore on its row", async () => {
    stub([], [document()], (url) =>
      url.endsWith("/restore")
        ? jsonResponse({ code: "knowledge.duplicate", detail: "dup" }, 400)
        : undefined,
    );

    renderWithClient(<TrashView />);
    const row = await screen.findByTestId(`trash-document-${DOC_ID}`);
    await userEvent.click(within(row).getByRole("button", { name: "Geri yükle" }));

    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "Aynı içerik bu çalışma alanında zaten var.",
    );
  });

  it("requests a gated purge of the selection only after confirmation, never deleting directly", async () => {
    const fetchMock = stub([task()], [document()], (url, init) =>
      url === "/api/approvals/requests" && init?.method === "POST"
        ? jsonResponse({ id: "pr1", workspace: "w1", tool_name: "purge_trash", status: "pending" }, 201)
        : undefined,
    );

    renderWithClient(<TrashView />);
    await screen.findByText("Süt al");
    const purge = screen.getByRole("button", { name: "Seçilenleri kalıcı olarak sil" });
    expect(purge).toBeDisabled();

    await userEvent.click(screen.getByRole("checkbox", { name: "Seç: Süt al" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Seç: Toplantı notu" }));
    expect(screen.getByText("2 öğe seçildi")).toBeInTheDocument();

    await userEvent.click(purge);
    const dialog = await findConfirmDialog();
    expect(dialog).toHaveTextContent("Seçilen 2 öğe için bir onay isteği oluşturulur");
    expect(within(dialog).getByText("Süt al")).toBeInTheDocument();
    await cancelDialog();
    expect(calledWith(fetchMock, "POST", "/api/approvals/requests")).toBe(false);

    await userEvent.click(purge);
    await confirmInDialog("Onay isteği oluştur");

    expect(await screen.findByRole("link", { name: "Onay isteğini aç" })).toHaveAttribute(
      "href",
      "/approvals/pr1",
    );
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(post?.[1]?.body as string)).toEqual({
      workspace: "w1",
      tool_name: "purge_trash",
      payload: { task_ids: [TASK_ID], document_ids: [DOC_ID] },
    });
    // No DELETE anywhere: the purge itself only runs on an approval decision.
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    // The rows stay in the trash until the decision.
    expect(screen.getByText("Süt al")).toBeInTheDocument();
    expect(screen.getByText("0 öğe seçildi")).toBeInTheDocument();
  });

  it("selects and clears everything", async () => {
    stub([task()], [document()]);
    renderWithClient(<TrashView />);
    await screen.findByText("Süt al");
    await userEvent.click(screen.getByRole("button", { name: "Tümünü seç" }));
    expect(screen.getByText("2 öğe seçildi")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Seçimi temizle" }));
    expect(screen.getByText("0 öğe seçildi")).toBeInTheDocument();
  });

  it("shows a load error instead of an empty trash", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ code: "channel_forbidden" }, 403)));
    renderWithClient(<TrashView />);
    expect(await screen.findByText("Çöp kutusu yüklenemedi.")).toBeInTheDocument();
    expect(screen.queryByText("Çöp kutusu boş.")).not.toBeInTheDocument();
  });
});
