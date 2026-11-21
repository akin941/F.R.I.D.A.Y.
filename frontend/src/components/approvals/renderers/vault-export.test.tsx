import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { rendererFor } from "@/components/approvals/renderers";
import { approvalRequest } from "@/components/approvals/test-fixtures";
import type { ApiVaultExportExecution } from "@/types/api";

// Phase 8B branch 9 (D12): the vault run detail names the conflict count
// and the folder(s) the owner-edited notes were saved to.

function execution(overrides: Partial<ApiVaultExportExecution> = {}): ApiVaultExportExecution {
  return {
    status: "succeeded",
    error_type: "",
    error_message: "",
    written_count: 3,
    updated_count: 1,
    removed_count: 0,
    retry_count: 0,
    conflicts_saved: 0,
    conflicts_private: 0,
    conflicts_folders: [],
    ...overrides,
  };
}

function renderExecution(exec: ApiVaultExportExecution) {
  const { Execution } = rendererFor("vault_export");
  if (!Execution) throw new Error("vault_export has an execution renderer");
  return render(<Execution request={approvalRequest({ tool_name: "vault_export", execution: exec })} />);
}

describe("vault export run detail", () => {
  it("renders the conflict count and both folders", () => {
    renderExecution(
      execution({
        conflicts_saved: 3,
        conflicts_private: 1,
        conflicts_folders: ["_conflicts/2026-11-20/", "_private/_conflicts/2026-11-20/"],
      }),
    );
    const line = screen.getByTestId("vault-conflicts");
    expect(line).toHaveTextContent("düzenlenmiş 3 not");
    expect(line).toHaveTextContent("`_conflicts/2026-11-20/` ve `_private/_conflicts/2026-11-20/`");
  });

  it("says there was no conflict", () => {
    renderExecution(execution());
    expect(screen.getByTestId("vault-conflicts")).toHaveTextContent("Çakışma yok");
  });

  it("an older run without the fields shows no conflict line", () => {
    const exec = execution();
    delete exec.conflicts_saved;
    renderExecution(exec);
    expect(screen.queryByTestId("vault-conflicts")).not.toBeInTheDocument();
  });

  it("a failed run shows no conflict line", () => {
    renderExecution(execution({ status: "failed", error_message: "x", conflicts_saved: 2 }));
    expect(screen.queryByTestId("vault-conflicts")).not.toBeInTheDocument();
  });
});
