import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkspaceProvider, useWorkspace } from "@/contexts/workspace-context";
import { WorkspaceSwitcher } from "@/components/shell/WorkspaceSwitcher";

function Active() {
  const { activeWorkspace } = useWorkspace();
  return <div data-testid="active">{activeWorkspace?.id ?? "none"}</div>;
}

function session(isOwner: boolean, withFirm: boolean) {
  return {
    user: { id: "u1", email: "o@test.dev", name: "Owner", is_agent: false, is_owner: isOwner },
    workspaces: [
      { id: "personal-1", name: "Owner", type: "personal", role: "owner" },
      ...(withFirm ? [{ id: "firm-1", name: "Acme", type: "company", role: "owner" }] : []),
    ],
  };
}

describe("Yeni firma", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a firm, refreshes the workspace list and switches to it", async () => {
    let created = false;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/workspaces" && init?.method === "POST") {
        created = true;
        return new Response(
          JSON.stringify({ id: "firm-1", name: "Acme", type: "company", role: "owner" }),
          { status: 201 },
        );
      }
      return new Response(JSON.stringify(session(true, created)), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WorkspaceProvider>
        <WorkspaceSwitcher />
        <Active />
      </WorkspaceProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Yeni firma" }));
    await userEvent.type(screen.getByLabelText("Firma adı"), "Acme");
    await userEvent.type(screen.getByLabelText("Notlar (isteğe bağlı)"), "Muhasebe");
    await userEvent.click(screen.getByRole("button", { name: "Oluştur" }));

    await waitFor(() => expect(screen.getByTestId("active")).toHaveTextContent("firm-1"));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(post[1]!.body as string)).toEqual({
      name: "Acme",
      profile: { notes: "Muhasebe" },
    });
  });

  it("is offered to the owner only", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(session(false, false)), { status: 200 })),
    );
    render(
      <WorkspaceProvider>
        <WorkspaceSwitcher />
      </WorkspaceProvider>,
    );
    await screen.findByRole("combobox", { name: "Aktif çalışma alanı" });
    expect(screen.queryByRole("button", { name: "Yeni firma" })).not.toBeInTheDocument();
  });
});
