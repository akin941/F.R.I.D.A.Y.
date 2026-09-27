import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { useWorkspace, WorkspaceProvider } from "@/contexts/workspace-context";

function Consumer() {
  const { activeWorkspace, loading, setActiveWorkspaceId, workspaces } = useWorkspace();
  if (loading) return <div>loading</div>;
  return (
    <div>
      <div data-testid="active">{activeWorkspace?.id ?? "none"}</div>
      <button onClick={() => setActiveWorkspaceId(workspaces[1]?.id ?? "")}>switch</button>
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
    window.localStorage.setItem("jarvis:active-workspace:u1", "deleted-workspace");
    render(
      <WorkspaceProvider>
        <Consumer />
      </WorkspaceProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("personal-1"));
  });

  it("honors a saved workspace id that still exists", async () => {
    window.localStorage.setItem("jarvis:active-workspace:u1", "company-1");
    render(
      <WorkspaceProvider>
        <Consumer />
      </WorkspaceProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("company-1"));
  });
});
