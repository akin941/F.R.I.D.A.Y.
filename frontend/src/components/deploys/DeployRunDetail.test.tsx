import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DeployRunDetail } from "@/components/deploys/DeployRunDetail";
import { RUN_ID, SHA_LIVE, SHA_NEW, deployRunDetail, jsonResponse } from "@/components/deploys/test-fixtures";
import { calledWith, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import type { ApiDeployRunDetail } from "@/types/api";

const URL = `/api/deploys/runs/${RUN_ID}`;

function stub(run: ApiDeployRunDetail) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if ((init?.method ?? "GET") === "GET" && url === URL) return jsonResponse(run);
    if (init?.method === "POST" && url === `${URL}/cancel`) {
      return jsonResponse({ ...run, status: "cancelled" });
    }
    throw new Error(`Unexpected fetch ${init?.method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("DeployRunDetail", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows SHAs, the state banner and health checks; the remote log stays fenced", async () => {
    stub(deployRunDetail({ status: "rolled_back", error_code: "health_checks_failed", needs_attention: true }));
    renderWithClient(<DeployRunDetail runId={RUN_ID} workspaceId="w1" />);
    expect(await screen.findByTestId("deploy-banner-rolled_back")).toHaveTextContent("Geri alındı");
    expect(screen.getByText(SHA_NEW)).toBeInTheDocument();
    expect(screen.getAllByText(SHA_LIVE).length).toBeGreaterThan(0);
    expect(screen.getByText("1: geçti")).toBeInTheDocument();
    expect(screen.getByText("2: başarısız")).toBeInTheDocument();
    // Low-trust remote output is hidden until explicitly opened...
    expect(screen.queryByText(/IGNORE PREVIOUS INSTRUCTIONS/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Uzak sunucu çıktısını göster" }));
    // ...and then shown as plain preformatted text under a warning.
    const output = screen.getByText(/IGNORE PREVIOUS INSTRUCTIONS/);
    expect(output.tagName).toBe("PRE");
    expect(screen.getByText(/güvenilmeyen veri/)).toBeInTheDocument();
  });

  it("a run before the deploy verb can be cancelled, only from the confirmation", async () => {
    const fetchMock = stub(deployRunDetail({ status: "pending", finished_at: null }));
    renderWithClient(<DeployRunDetail runId={RUN_ID} workspaceId="w1" />);
    await userEvent.click(await screen.findByRole("button", { name: "İptal et" }));
    await findConfirmDialog();
    expect(calledWith(fetchMock, "POST", `${URL}/cancel`)).toBe(false);
    await confirmInDialog("Evet, iptal et");
    await waitFor(() => expect(calledWith(fetchMock, "POST", `${URL}/cancel`)).toBe(true));
    expect(await screen.findByTestId("deploy-banner-cancelled")).toBeInTheDocument();
  });

  it.each(["deploying", "health_checking", "succeeded", "unknown_outcome"])(
    "no cancel once the deploy verb may have been sent (%s)",
    async (status) => {
      stub(deployRunDetail({ status: status as never }));
      renderWithClient(<DeployRunDetail runId={RUN_ID} workspaceId="w1" />);
      await screen.findByTestId(`deploy-banner-${status}`);
      expect(screen.queryByRole("button", { name: "İptal et" })).not.toBeInTheDocument();
    },
  );
});
