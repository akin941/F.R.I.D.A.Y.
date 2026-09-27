import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { WorkspaceDeepLink } from "@/components/shell/WorkspaceDeepLink";

const { state } = vi.hoisted(() => ({
  state: { params: new URLSearchParams("workspace=w2"), loading: true, follow: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => state.params,
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ loading: state.loading, followWorkspaceLink: state.follow }),
}));

describe("WorkspaceDeepLink", () => {
  it("leaves the initial link to the provider and follows later changes only", () => {
    const { rerender } = render(<WorkspaceDeepLink />);

    state.loading = false;
    rerender(<WorkspaceDeepLink />);
    // The first value after load was already applied by WorkspaceProvider.
    expect(state.follow).not.toHaveBeenCalled();

    rerender(<WorkspaceDeepLink />);
    expect(state.follow).not.toHaveBeenCalled();

    state.params = new URLSearchParams("workspace=w3");
    rerender(<WorkspaceDeepLink />);
    expect(state.follow).toHaveBeenCalledExactlyOnceWith("w3");

    state.params = new URLSearchParams("");
    rerender(<WorkspaceDeepLink />);
    expect(state.follow).toHaveBeenCalledTimes(1);
  });
});
