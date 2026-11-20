import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PullRequestPanel } from "@/components/harness/PullRequestPanel";
import { JOB_ID, jsonResponse, prState } from "@/components/deploys/test-fixtures";
import { calledWith, cancelDialog, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import type { ApiPullRequestState } from "@/types/api";

const URL = `/api/harness/implementation-jobs/${JOB_ID}/pull-request`;

function stub(state: ApiPullRequestState, after?: ApiPullRequestState) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url !== URL) throw new Error(`Unexpected fetch ${url}`);
    if (init?.method === "POST") return jsonResponse(after ?? state, 201);
    return jsonResponse(state);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("PullRequestPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("'PR aç' only mints the gate from its confirmation, with the edited title", async () => {
    const fetchMock = stub(
      prState(),
      prState({ can_request: false, request: { id: "r7", status: "pending", created_at: "x" } }),
    );
    renderWithClient(<PullRequestPanel jobId={JOB_ID} workspaceId="w1" />);
    await userEvent.click(await screen.findByRole("button", { name: "PR aç" }));
    const dialog = await findConfirmDialog();
    expect(dialog).toHaveTextContent("Friday PR'ı birleştirmez");
    expect(calledWith(fetchMock, "POST", URL)).toBe(false);
    await cancelDialog();
    expect(calledWith(fetchMock, "POST", URL)).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "PR aç" }));
    await userEvent.type(within(await findConfirmDialog()).getByLabelText("PR başlığı"), "Add health");
    await confirmInDialog("Onay isteği oluştur");
    await waitFor(() => expect(calledWith(fetchMock, "POST", URL)).toBe(true));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(post[1]!.body as string)).toEqual({ title: "Add health" });
    expect(await screen.findByRole("link", { name: "Onaylar sayfasında karar ver" })).toHaveAttribute(
      "href",
      "/approvals/r7",
    );
    expect(screen.queryByRole("button", { name: "PR aç" })).not.toBeInTheDocument();
  });

  it("shows the opened PR, its link and the CI state", async () => {
    stub(
      prState({
        can_request: false,
        request: { id: "r7", status: "approved", created_at: "x" },
        execution: { status: "succeeded", error_type: "", finished_at: "y" },
        pull_request: { number: 42, url: "https://github.com/acme/adia-web/pull/42" },
        ci: { state: "failure", runs: [{ name: "CI", status: "completed", conclusion: "failure", url: "" }] },
      }),
    );
    renderWithClient(<PullRequestPanel jobId={JOB_ID} workspaceId="w1" />);
    expect(await screen.findByText("PR #42 açıldı")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "GitHub'da aç" })).toHaveAttribute(
      "href",
      "https://github.com/acme/adia-web/pull/42",
    );
    expect(screen.getByText("CI başarısız")).toBeInTheDocument();
    expect(screen.getByText("Gönderildi")).toBeInTheDocument();
  });

  it("never links a non-GitHub URL; an unknown outcome is flagged", async () => {
    stub(
      prState({
        can_request: false,
        request: { id: "r7", status: "approved", created_at: "x" },
        execution: { status: "unknown_outcome", error_type: "worker_lost", finished_at: null },
        pull_request: { number: 1, url: "https://evil.example/pull/1" },
        ci: { state: "unknown", runs: [], error: "timeout" },
      }),
    );
    renderWithClient(<PullRequestPanel jobId={JOB_ID} workspaceId="w1" />);
    await screen.findByText("PR #1 açıldı");
    expect(screen.queryByRole("link", { name: "GitHub'da aç" })).not.toBeInTheDocument();
    expect(screen.getByText(/asla yeniden göndermez/)).toBeInTheDocument();
    expect(screen.getByText(/CI durumu okunamadı: Zaman aşımı/)).toBeInTheDocument();
  });
});
