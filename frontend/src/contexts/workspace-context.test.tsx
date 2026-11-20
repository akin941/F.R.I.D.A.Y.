import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { useWorkspace, WorkspaceProvider } from "@/contexts/workspace-context";

const { toastMock } = vi.hoisted(() => ({
  toastMock: { info: vi.fn(), error: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: toastMock }));

function Consumer() {
  const { activeWorkspace, loading, setActiveWorkspaceId, workspaces, followWorkspaceLink } =
    useWorkspace();
  if (loading) return <div>loading</div>;
  return (
    <div>
      <div data-testid="active">{activeWorkspace?.id ?? "none"}</div>
      <button onClick={() => setActiveWorkspaceId(workspaces[1]?.id ?? "")}>switch</button>
      <button onClick={() => followWorkspaceLink("company-1")}>follow</button>
      <button onClick={() => followWorkspaceLink("stranger-ws")}>follow-unknown</button>
    </div>
  );
}

const SESSION_BODY = {
  user: { id: "u1", email: "alice@test.dev", name: "Alice", is_agent: false, is_owner: true },
  workspaces: [
    { id: "personal-1", name: "Alice", type: "personal", role: "owner" },
    { id: "company-1", name: "Acme", type: "company", role: "member" },
  ],
};

describe("WorkspaceProvider", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(SESSION_BODY), { status: 200 })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    toastMock.info.mockReset();
    toastMock.error.mockReset();
  });

  it("defaults to the personal workspace when no preference is saved", async () => {
    render(
      <WorkspaceProvider>
        <Consumer />
      </WorkspaceProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("personal-1"));
  });

  it("falls back to the personal workspace when the saved id no longer exists", async () => {
    window.localStorage.setItem("friday:active-workspace:u1", "deleted-workspace");
    render(
      <WorkspaceProvider>
        <Consumer />
      </WorkspaceProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("personal-1"));
  });

  it("honors a saved workspace id that still exists", async () => {
    window.localStorage.setItem("friday:active-workspace:u1", "company-1");
    render(
      <WorkspaceProvider>
        <Consumer />
      </WorkspaceProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("company-1"));
  });

  describe("?workspace= deep links", () => {
    it("opens the linked workspace on load (over the saved one) with a toast", async () => {
      window.localStorage.setItem("friday:active-workspace:u1", "personal-1");
      window.history.replaceState(null, "", "/approvals?workspace=company-1");
      render(
        <WorkspaceProvider>
          <Consumer />
        </WorkspaceProvider>,
      );
      await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("company-1"));
      expect(toastMock.info).toHaveBeenCalledWith("Bağlantı “Acme” çalışma alanını açtı.");
      expect(window.localStorage.getItem("friday:active-workspace:u1")).toBe("company-1");
    });

    it("stays put and explains when the linked workspace is not accessible", async () => {
      window.history.replaceState(null, "", "/approvals?workspace=stranger-ws");
      render(
        <WorkspaceProvider>
          <Consumer />
        </WorkspaceProvider>,
      );
      await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("personal-1"));
      expect(toastMock.error).toHaveBeenCalledTimes(1);
      expect(toastMock.info).not.toHaveBeenCalled();
    });

    it("does not toast when the link names the already-active workspace", async () => {
      window.history.replaceState(null, "", "/agents?workspace=personal-1");
      render(
        <WorkspaceProvider>
          <Consumer />
        </WorkspaceProvider>,
      );
      await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("personal-1"));
      expect(toastMock.info).not.toHaveBeenCalled();
      expect(toastMock.error).not.toHaveBeenCalled();
    });

    it("follows a link after load (client navigation)", async () => {
      render(
        <WorkspaceProvider>
          <Consumer />
        </WorkspaceProvider>,
      );
      await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("personal-1"));

      screen.getByRole("button", { name: "follow-unknown" }).click();
      expect(toastMock.error).toHaveBeenCalledTimes(1);

      screen.getByRole("button", { name: "follow" }).click();
      await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("company-1"));
      expect(toastMock.info).toHaveBeenCalledTimes(1);
    });
  });
});
