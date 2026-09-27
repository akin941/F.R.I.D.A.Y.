import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HarnessRepoRegistry } from "@/components/harness/HarnessRepoRegistry";
import { REPO_2_ID, REPO_ID, jsonResponse, repo } from "@/components/harness/test-fixtures";
import { calledWith, cancelDialog, confirmInDialog, findConfirmDialog } from "@/test/confirm";
import { renderWithClient } from "@/test/render";
import type { ApiHarnessRepo } from "@/types/api";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({
    activeWorkspace: { id: "w1", name: "Alice", type: "personal", role: "owner" },
  }),
}));

const LIST_URL = "/api/harness/repos?workspace=w1";

function stub(
  repos: ApiHarnessRepo[],
  extra: (url: string, init?: RequestInit) => Response | undefined = () => undefined,
) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const handled = extra(url, init);
    if (handled) return handled;
    if (url === LIST_URL) return jsonResponse(repos);
    throw new Error(`Unexpected fetch ${init?.method ?? "GET"} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function card(id: string) {
  return screen.getByTestId(`repo-${id}`);
}

describe("HarnessRepoRegistry", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists repos with their state and the read-only self-repo status", async () => {
    stub([
      repo({ self_repo_status: "self", sensitive: true }),
      repo({
        id: REPO_2_ID,
        name: "site",
        enabled: false,
        test_command: "",
        self_repo_status: "other",
      }),
    ]);

    renderWithClient(<HarnessRepoRegistry />);

    await screen.findByText("site");
    const jarvis = card(REPO_ID);
    expect(within(jarvis).getByText("Etkin")).toBeInTheDocument();
    expect(within(jarvis).getByText("Hassas")).toBeInTheDocument();
    expect(within(jarvis).getByText("Jarvis'in kendi deposu")).toBeInTheDocument();
    expect(within(jarvis).getByText("make test")).toBeInTheDocument();
    const site = card(REPO_2_ID);
    expect(within(site).getByText("Devre dışı")).toBeInTheDocument();
    expect(within(site).getByText(/Test komutu tanımlı değil/)).toBeInTheDocument();
    expect(within(site).queryByText("Jarvis'in kendi deposu")).not.toBeInTheDocument();
    // Nothing in the UI edits the guard's view of a repo.
    expect(screen.queryByText(/HARNESS_SELF_REPO_IDENTIFIERS/)).not.toBeInTheDocument();
  });

  it("warns when the self-repo guard is unconfigured", async () => {
    stub([repo({ self_repo_status: "unconfigured" })]);
    renderWithClient(<HarnessRepoRegistry />);
    expect(await screen.findByText(/HARNESS_SELF_REPO_IDENTIFIERS tanımlı değil/)).toBeInTheDocument();
  });

  it("shows the curator-only refusal as an error, not an empty registry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ code: "harness.permission_denied" }, 403)),
    );
    renderWithClient(<HarnessRepoRegistry />);
    expect(await screen.findByText("Depolar yüklenemedi.")).toBeInTheDocument();
    expect(screen.queryByText("Henüz kayıtlı depo yok.")).not.toBeInTheDocument();
  });

  it("registers a repo", async () => {
    const created = repo({ id: REPO_2_ID, name: "site", git_url: "git@example.test:me/site.git" });
    const fetchMock = stub([], (url, init) =>
      url === "/api/harness/repos" && init?.method === "POST" ? jsonResponse(created, 201) : undefined,
    );

    renderWithClient(<HarnessRepoRegistry />);
    await screen.findByText("Henüz kayıtlı depo yok.");

    await userEvent.click(screen.getByRole("button", { name: "Depo kaydet" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Ad"), "site");
    await userEvent.type(within(dialog).getByLabelText("Git adresi"), "git@example.test:me/site.git");
    await userEvent.type(within(dialog).getByLabelText("Test komutu"), "npm test");
    await userEvent.click(within(dialog).getByRole("checkbox", { name: /^Etkin/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Kaydet" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("site")).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(post?.[1]?.body as string)).toEqual({
      workspace: "w1",
      name: "site",
      git_url: "git@example.test:me/site.git",
      default_branch: "main",
      enabled: true,
      sensitive: false,
      test_command: "npm test",
    });
  });

  it("keeps the form open with a Turkish error when the backend rejects it", async () => {
    stub([], (url, init) =>
      url === "/api/harness/repos" && init?.method === "POST"
        ? jsonResponse({ git_url: ["git_url must not embed credentials"], code: "invalid" }, 400)
        : undefined,
    );

    renderWithClient(<HarnessRepoRegistry />);
    await screen.findByText("Henüz kayıtlı depo yok.");
    await userEvent.click(screen.getByRole("button", { name: "Depo kaydet" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Ad"), "x");
    await userEvent.type(within(dialog).getByLabelText("Git adresi"), "https://tok@host/r");
    await userEvent.click(within(dialog).getByRole("button", { name: "Kaydet" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Depo kaydedilemedi.");
    expect(screen.queryByText(/must not embed/)).not.toBeInTheDocument();
  });

  it("edits a repo (e.g. disables it) through PATCH", async () => {
    const fetchMock = stub([repo()], (url, init) =>
      url === `/api/harness/repos/${REPO_ID}` && init?.method === "PATCH"
        ? jsonResponse(repo({ enabled: false }))
        : undefined,
    );

    renderWithClient(<HarnessRepoRegistry />);
    await screen.findByText("jarvis");
    await userEvent.click(within(card(REPO_ID)).getByRole("button", { name: "Düzenle" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Ad")).toHaveValue("jarvis");
    await userEvent.click(within(dialog).getByRole("checkbox", { name: /^Etkin/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Değişiklikleri kaydet" }));

    await waitFor(() => expect(within(card(REPO_ID)).getByText("Devre dışı")).toBeInTheDocument());
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(JSON.parse(patch?.[1]?.body as string)).toMatchObject({ enabled: false, name: "jarvis" });
  });

  it("deletes a repo only after confirmation, and shows a refusal inside the dialog", async () => {
    let refuse = true;
    const fetchMock = stub([repo()], (url, init) => {
      if (url === `/api/harness/repos/${REPO_ID}` && init?.method === "DELETE") {
        return refuse
          ? jsonResponse({ code: "harness.invalid_transition", detail: "has history" }, 400)
          : new Response(null, { status: 204 });
      }
      return undefined;
    });

    renderWithClient(<HarnessRepoRegistry />);
    await screen.findByText("jarvis");
    await userEvent.click(within(card(REPO_ID)).getByRole("button", { name: "Sil" }));
    expect(await findConfirmDialog()).toHaveTextContent("“jarvis” harness kayıtlarından kaldırılır");
    await cancelDialog();
    expect(calledWith(fetchMock, "DELETE", `/api/harness/repos/${REPO_ID}`)).toBe(false);

    await userEvent.click(within(card(REPO_ID)).getByRole("button", { name: "Sil" }));
    await confirmInDialog("Evet, sil");
    const dialog = await findConfirmDialog();
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Bu harness işi, deposu ya da ajanı artık bu işleme uygun değil.",
    );

    refuse = false;
    await confirmInDialog("Evet, sil");
    await waitFor(() => expect(screen.queryByText("jarvis")).not.toBeInTheDocument());
  });
});
