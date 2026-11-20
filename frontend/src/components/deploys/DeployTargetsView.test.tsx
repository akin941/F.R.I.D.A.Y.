import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DeployTargetsView } from "@/components/deploys/DeployTargetsView";
import {
  SHA_LIVE,
  SHA_NEW,
  TARGET_ID,
  branchHead,
  deployRun,
  deployTarget,
  jsonResponse,
} from "@/components/deploys/test-fixtures";
import { calledWith, cancelDialog, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import type { ApiDeployBranchHead, ApiDeployRun, ApiDeployTarget } from "@/types/api";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({
    activeWorkspace: { id: "w1", name: "Acme", type: "company", role: "owner" },
  }),
}));

const TARGETS_URL = "/api/deploys/targets?workspace=w1";
const RUNS_URL = "/api/deploys/runs?workspace=w1";
const HEAD_URL = `/api/deploys/targets/${TARGET_ID}/head`;

function stub({
  targets = [deployTarget()],
  runs = [] as ApiDeployRun[],
  head = branchHead() as ApiDeployBranchHead,
}: { targets?: ApiDeployTarget[]; runs?: ApiDeployRun[]; head?: ApiDeployBranchHead } = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (method === "GET" && url === TARGETS_URL) return jsonResponse(targets);
    if (method === "GET" && url === RUNS_URL) return jsonResponse(runs);
    if (method === "GET" && url === HEAD_URL) return jsonResponse(head);
    if (method === "POST" && url === "/api/deploys/requests") {
      return jsonResponse({ id: "r9", tool_name: "deploy", status: "pending" }, 201);
    }
    if (method === "PATCH" && url === `/api/deploys/targets/${TARGET_ID}`) {
      return jsonResponse({ ...targets[0], enabled: !targets[0]!.enabled });
    }
    if (method === "POST" && url === `/api/deploys/targets/${TARGET_ID}/status`) {
      return new Response(null, { status: 202 });
    }
    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function postedRequests(fetchMock: ReturnType<typeof stub>) {
  return fetchMock.mock.calls.filter(
    ([url, init]) => url === "/api/deploys/requests" && init?.method === "POST",
  );
}

async function openDeployDialog() {
  const card = await screen.findByTestId(`target-${TARGET_ID}`);
  await userEvent.click(within(card).getByRole("button", { name: "Deploy" }));
  return screen.findByRole("dialog");
}

describe("DeployTargetsView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists a target with its live SHA, settings and recent runs with attention banners", async () => {
    stub({
      runs: [
        deployRun({ id: "run-a", status: "succeeded_degraded", needs_attention: true, error_code: "health_url_failed" }),
        deployRun({ id: "run-b", status: "succeeded" }),
      ],
    });
    renderWithClient(<DeployTargetsView />);
    const card = await screen.findByTestId(`target-${TARGET_ID}`);
    expect(within(card).getByText("ADİA")).toBeInTheDocument();
    expect(within(card).getByText(SHA_LIVE)).toBeInTheDocument();
    expect(within(card).getByText("deploy@203.0.113.7")).toBeInTheDocument();
    await waitFor(() => expect(within(card).getByTestId("run-run-a")).toBeInTheDocument());
    expect(within(card).getByTestId("deploy-banner-succeeded_degraded")).toBeInTheDocument();
    // A clean run carries no banner in the list.
    expect(within(card).queryByTestId("deploy-banner-succeeded")).not.toBeInTheDocument();
  });

  it("Deploy dialog: prefilled SHA and CI state, nothing sent until the confirmation", async () => {
    const fetchMock = stub();
    renderWithClient(<DeployTargetsView />);
    const dialog = await openDeployDialog();
    const input = await within(dialog).findByDisplayValue(SHA_NEW);
    expect(input).toBeInTheDocument();
    expect(within(dialog).getByText("CI başarılı")).toBeInTheDocument();
    expect(within(dialog).getByText("Ship the header")).toBeInTheDocument();
    expect(within(dialog).getByText(/dalının ucu GitHub'dan dolduruldu/)).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Devam et" }));
    const confirm = await findConfirmDialog();
    expect(confirm).toHaveTextContent(SHA_NEW);
    expect(postedRequests(fetchMock)).toHaveLength(0);

    // Backing out of the confirmation sends nothing.
    await cancelDialog();
    expect(postedRequests(fetchMock)).toHaveLength(0);

    await userEvent.click(within(dialog).getByRole("button", { name: "Devam et" }));
    await confirmInDialog("Onay isteği oluştur");
    await waitFor(() => expect(postedRequests(fetchMock)).toHaveLength(1));
    const body = JSON.parse(postedRequests(fetchMock)[0]![1]!.body as string);
    expect(body).toEqual({ target: TARGET_ID, sha: SHA_NEW });
    expect(await screen.findByRole("link", { name: "Onaylar sayfasında karar ver" })).toHaveAttribute(
      "href",
      "/approvals/r9",
    );
  });

  it("the manual path: the owner types the SHA; an invalid or live SHA never reaches the confirmation", async () => {
    const fetchMock = stub({
      head: branchHead({ sha: "", title: "", ci_conclusion: "unknown", source: "manual" }),
    });
    renderWithClient(<DeployTargetsView />);
    const dialog = await openDeployDialog();
    expect(await within(dialog).findByText(/SHA'yı kendin yaz/)).toBeInTheDocument();
    expect(within(dialog).getByText("CI durumu bilinmiyor")).toBeInTheDocument();

    const input = within(dialog).getByLabelText("Commit SHA");
    await userEvent.click(within(dialog).getByRole("button", { name: "Devam et" }));
    expect(within(dialog).getByText(/Geçerli bir commit SHA/)).toBeInTheDocument();

    await userEvent.type(input, SHA_LIVE.slice(0, 10));
    await userEvent.click(within(dialog).getByRole("button", { name: "Devam et" }));
    expect(within(dialog).getByText("Bu commit zaten canlıda.")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    await userEvent.clear(input);
    await userEvent.type(input, "ABCDEF1234");
    await userEvent.click(within(dialog).getByRole("button", { name: "Devam et" }));
    await confirmInDialog("Onay isteği oluştur");
    await waitFor(() => expect(postedRequests(fetchMock)).toHaveLength(1));
    expect(JSON.parse(postedRequests(fetchMock)[0]![1]!.body as string).sha).toBe("abcdef1234");
  });

  it("a disabled or busy target offers no Deploy", async () => {
    stub({ targets: [deployTarget({ enabled: false })] });
    renderWithClient(<DeployTargetsView />);
    const card = await screen.findByTestId(`target-${TARGET_ID}`);
    expect(within(card).getByRole("button", { name: "Deploy" })).toBeDisabled();
  });

  it("enable/disable goes through a confirmation", async () => {
    const fetchMock = stub();
    renderWithClient(<DeployTargetsView />);
    const card = await screen.findByTestId(`target-${TARGET_ID}`);
    await userEvent.click(within(card).getByRole("button", { name: "Devre dışı bırak" }));
    await findConfirmDialog();
    expect(calledWith(fetchMock, "PATCH", `/api/deploys/targets/${TARGET_ID}`)).toBe(false);
    await cancelDialog();
    await userEvent.click(within(card).getByRole("button", { name: "Devre dışı bırak" }));
    await confirmInDialog("Devre dışı bırak");
    await waitFor(() =>
      expect(calledWith(fetchMock, "PATCH", `/api/deploys/targets/${TARGET_ID}`)).toBe(true),
    );
  });

  it("a status refresh queues the read-only status verb", async () => {
    const fetchMock = stub({ targets: [deployTarget({ current_sha: "" })] });
    renderWithClient(<DeployTargetsView />);
    const card = await screen.findByTestId(`target-${TARGET_ID}`);
    expect(within(card).getByText(/Bilinmiyor — durum kontrolü çalıştır/)).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Durum kontrolü" }));
    await waitFor(() =>
      expect(calledWith(fetchMock, "POST", `/api/deploys/targets/${TARGET_ID}/status`)).toBe(true),
    );
  });

  it("shows an owner-only refusal as an error state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ code: "owner_required" }, 403)));
    renderWithClient(<DeployTargetsView />);
    expect(await screen.findByText("Deploy hedefleri yüklenemedi.")).toBeInTheDocument();
  });
});
