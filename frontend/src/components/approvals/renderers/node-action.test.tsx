import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RENDERED_TOOL_NAMES, rendererFor } from "@/components/approvals/renderers";
import { approvalRequest } from "@/components/approvals/test-fixtures";
import { tr } from "@/lib/i18n/tr";
import type { ApiApprovalRequest, ApiNodeActionDisplaySummary } from "@/types/api";

function summary(overrides: Partial<ApiNodeActionDisplaySummary> = {}): ApiNodeActionDisplaySummary {
  return {
    kind: "node_action",
    title: "Repoda komut çalıştır",
    capability: "repo.command.run",
    node_name: "MacBook",
    repo_name: "acme",
    trust_verdict: "warn",
    trust_reason: "trust_check_pending",
    voice: false,
    note: "Mac'teki onay penceresi kendi metnini gösterir",
    expires_in_seconds: 600,
    command_kind: "test",
    ...overrides,
  };
}

function renderRequest(request: ApiApprovalRequest) {
  const { Summary, Execution } = rendererFor(request.tool_name);
  return render(
    <>
      <Summary request={request} />
      <Execution request={request} />
    </>,
  );
}

describe("node_action renderer (Phase 10 D6)", () => {
  it("is the dedicated renderer", () => {
    expect(rendererFor("node_action").toolName).toBe("node_action");
    expect(RENDERED_TOOL_NAMES).toContain("node_action");
    expect(rendererFor("node_action").title(approvalRequest({ tool_name: "node_action" }))).toBe(
      tr.approvals.toolTitles.node_action,
    );
  });

  it("renders the whitelisted summary: label, repo name, command, trust, the Mac note, the TTL", () => {
    renderRequest(approvalRequest({ tool_name: "node_action", display_summary: summary() }));
    const card = screen.getByTestId("node-action-summary");
    expect(card).toHaveTextContent("Repoda komut çalıştır");
    expect(card).toHaveTextContent("MacBook");
    expect(card).toHaveTextContent("acme");
    expect(card).toHaveTextContent("test");
    expect(card).toHaveTextContent("dikkat");
    expect(card).toHaveTextContent("trust_check_pending");
    expect(card).toHaveTextContent("Mac'teki onay penceresi kendi metnini gösterir");
    expect(card).toHaveTextContent(tr.approvals.nodeAction.expires(10));
    expect(screen.queryByTestId("node-action-voice")).toBeNull();
  });

  it("marks a voice proposal", () => {
    renderRequest(
      approvalRequest({
        tool_name: "node_action",
        display_summary: summary({ voice: true, voice_label: "Sesle önerildi" }),
      }),
    );
    expect(screen.getByTestId("node-action-voice")).toHaveTextContent("Sesle önerildi");
  });

  it("renders typed text as text, never markup", () => {
    renderRequest(
      approvalRequest({
        tool_name: "node_action",
        display_summary: summary({ capability: "ax.type_text", text: "<img src=x onerror=alert(1)>", submit: true }),
      }),
    );
    expect(screen.getByTestId("node-action-summary")).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(document.querySelector("img")).toBeNull();
  });

  it("shows the execution status and polls until it is terminal", () => {
    const running = approvalRequest({
      tool_name: "node_action",
      status: "approved",
      display_summary: summary(),
      execution: { status: "running", refusal_code: "", exit_code: null, confirm_state: "approved", finished_at: null },
    });
    renderRequest(running);
    expect(screen.getByTestId("node-action-execution")).toHaveTextContent("çalışıyor");
    expect(rendererFor("node_action").pollInterval(running)).toBe(2000);
    const done = { ...running, execution: { ...running.execution!, status: "failed", exit_code: 1 } } as ApiApprovalRequest;
    expect(rendererFor("node_action").pollInterval(done)).toBeNull();
  });

  it("states the consequence of each decision", () => {
    const request = approvalRequest({ tool_name: "node_action" });
    expect(rendererFor("node_action").consequence(request, "approve")).toContain("Mac'teki pencerede");
    expect(rendererFor("node_action").consequence(request, "reject")).toContain("hiçbir şey gönderilmez");
  });
});
