import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ImplementationJobForm } from "@/components/harness/ImplementationJobForm";
import { REPO_ID, implJob, jsonResponse, repo } from "@/components/harness/test-fixtures";

type FetchMock = ReturnType<typeof vi.fn<(input: string, init?: RequestInit) => Promise<Response>>>;

function stubFetch(postResponse: () => Response): FetchMock {
  const fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>(
    async (input, init) => {
      if (input.startsWith("/api/harness/repos")) return jsonResponse([repo()]);
      if (init?.method === "POST" && input === "/api/harness/implementation-jobs") {
        return postResponse();
      }
      throw new Error(`unexpected fetch ${input}`);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function postedBody(fetchMock: FetchMock) {
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
  return JSON.parse(call?.[1]?.body as string) as Record<string, unknown>;
}

describe("ImplementationJobForm", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("explains the two owner gates and the fixed Codex CLI reviewer", async () => {
    stubFetch(() => jsonResponse(implJob(), 201));

    render(
      <ImplementationJobForm workspaceId="w1" onCreated={() => undefined} onCancel={() => undefined} />,
    );

    expect(screen.getByText(/plan onayı \(Kapı A\)/)).toBeInTheDocument();
    expect(screen.getByText(/gönderim onayı \(Kapı B\)/)).toBeInTheDocument();
    expect(
      screen.getByText("Uygulayıcı: Claude Code · İnceleyici: Codex CLI (sabit; seçilemez)."),
    ).toBeInTheDocument();
    // The reviewer is fixed server-side -- no reviewer/agent picker exists.
    expect(await screen.findAllByRole("combobox")).toHaveLength(1);
  });

  it("creates a fresh implementation job without a discovery link", async () => {
    const fetchMock = stubFetch(() => jsonResponse(implJob(), 201));
    const onCreated = vi.fn();

    render(<ImplementationJobForm workspaceId="w1" onCreated={onCreated} onCancel={() => undefined} />);

    const submit = screen.getByRole("button", { name: "Uygulama iste" });
    expect(submit).toBeDisabled();

    await userEvent.click(await screen.findByRole("combobox", { name: "Depo" }));
    await userEvent.click(await screen.findByRole("option", { name: "jarvis" }));
    await userEvent.type(screen.getByLabelText("Uygulama hedefi"), "Add a health endpoint");
    await userEvent.click(submit);

    expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ id: "ij1" }));
    expect(postedBody(fetchMock)).toEqual({
      workspace: "w1",
      repo: REPO_ID,
      goal: "Add a health endpoint",
    });
  });

  it("prefills from a discovery job and sends discovery_job", async () => {
    const fetchMock = stubFetch(() => jsonResponse(implJob({ discovery_job: "d1" }), 201));
    const onCreated = vi.fn();

    render(
      <ImplementationJobForm
        workspaceId="w1"
        prefill={{ repo: REPO_ID, goal: "Find the flaky test", discoveryJobId: "d1" }}
        onCreated={onCreated}
        onCancel={() => undefined}
      />,
    );

    expect(screen.getByText(/Bir keşif işinden oluşturuluyor/)).toBeInTheDocument();
    expect(screen.getByLabelText("Uygulama hedefi")).toHaveValue("Find the flaky test");
    expect(await screen.findByRole("combobox", { name: "Depo" })).toHaveTextContent("jarvis");

    await userEvent.click(screen.getByRole("button", { name: "Uygulama iste" }));

    expect(onCreated).toHaveBeenCalled();
    expect(postedBody(fetchMock)).toEqual({
      workspace: "w1",
      repo: REPO_ID,
      goal: "Find the flaky test",
      discovery_job: "d1",
    });
  });

  it("shows the Turkish message for the backend's error code when creation is refused", async () => {
    stubFetch(() =>
      jsonResponse(
        { detail: "Repo has no test_command configured.", code: "harness.invalid_transition" },
        400,
      ),
    );
    const onCreated = vi.fn();

    render(
      <ImplementationJobForm
        workspaceId="w1"
        prefill={{ repo: REPO_ID, goal: "x", discoveryJobId: "d1" }}
        onCreated={onCreated}
        onCancel={() => undefined}
      />,
    );
    await screen.findByRole("combobox", { name: "Depo" });
    await userEvent.click(screen.getByRole("button", { name: "Uygulama iste" }));

    expect(
      await screen.findByText("Bu harness işi, deposu ya da ajanı artık bu işleme uygun değil."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Repo has no test_command configured.")).not.toBeInTheDocument();
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("caps the goal at the backend's 2000-character limit", () => {
    stubFetch(() => jsonResponse(implJob(), 201));

    render(
      <ImplementationJobForm workspaceId="w1" onCreated={() => undefined} onCancel={() => undefined} />,
    );

    expect(screen.getByLabelText("Uygulama hedefi")).toHaveAttribute("maxlength", "2000");
  });
});
