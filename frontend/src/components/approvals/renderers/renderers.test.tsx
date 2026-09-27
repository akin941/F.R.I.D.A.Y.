import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RENDERED_TOOL_NAMES, rendererFor } from "@/components/approvals/renderers";
import { genericRenderer } from "@/components/approvals/renderers/generic";
import {
  approvalRequest,
  implementSummary,
  pushSummary,
} from "@/components/approvals/test-fixtures";
import type { ApiApprovalRequest } from "@/types/api";

function renderSummary(request: ApiApprovalRequest) {
  const { Summary } = rendererFor(request.tool_name);
  return render(<Summary request={request} />);
}

describe("rendererFor", () => {
  it.each(["vault_export", "harness_implement", "harness_push", "purge_trash"])(
    "selects the dedicated renderer for %s",
    (tool) => {
      expect(rendererFor(tool).toolName).toBe(tool);
      expect(RENDERED_TOOL_NAMES).toContain(tool);
    },
  );

  it.each(["some_future_tool", "toString", "__proto__", ""])(
    "falls back to the generic renderer for %j",
    (tool) => {
      expect(rendererFor(tool)).toBe(genericRenderer);
    },
  );

  it("the generic fallback keeps the raw tool name, renders no summary, a status-only run line", () => {
    const request = approvalRequest({
      tool_name: "some_future_tool",
      display_summary: { kind: "harness_push" } as never,
      execution: { status: "running" } as never,
    });
    const renderer = rendererFor(request.tool_name);
    expect(renderer.title(request)).toBe("some_future_tool");
    const { container } = render(
      <>
        <renderer.Summary request={request} />
        <renderer.Execution request={request} />
      </>,
    );
    expect(container).toHaveTextContent("Çalışma durumu: Çalışıyor");
    expect(container).not.toHaveTextContent("Dal");
    expect(renderer.consequence(request, "approve")).toBe("Onaylarsan bu istek çalıştırılır.");
  });
});

describe("consequences", () => {
  it("Gate B names the branch that will be pushed", () => {
    const request = approvalRequest({
      tool_name: "harness_push",
      display_summary: pushSummary({ branch_name: "harness/p3-abc" }),
    });
    expect(rendererFor("harness_push").consequence(request, "approve")).toBe(
      "Onaylarsan “harness/p3-abc” dalı kayıtlı uzak depoya gönderilir (push). Gönderim geri alınamaz.",
    );
  });

  it("Gate A names the repo and branch the agent will work on", () => {
    const request = approvalRequest({
      tool_name: "harness_implement",
      display_summary: implementSummary(),
    });
    const text = rendererFor("harness_implement").consequence(request, "approve");
    expect(text).toContain("“jarvis” deposunda");
    expect(text).toContain("“harness/p3-ij1” dalı");
    expect(text).toContain("Kapı B");
  });

  it("purge names the counts and says it is irreversible", () => {
    const request = approvalRequest({
      tool_name: "purge_trash",
      display_summary: { kind: "purge_trash", task_count: 2, document_count: 1, titles: [] },
    });
    expect(rendererFor("purge_trash").consequence(request, "approve")).toContain(
      "2 görev ve 1 belge kalıcı olarak silinir",
    );
    expect(rendererFor("purge_trash").consequence(request, "reject")).toContain(
      "çöp kutusunda kalır",
    );
  });

  it("falls back to the tool's own wording without a summary (non-curator)", () => {
    const request = approvalRequest({ tool_name: "harness_push", display_summary: null });
    expect(rendererFor("harness_push").consequence(request, "approve")).toBe(
      "Onaylarsan bu dal uzak depoya gönderilir (push). Gönderim geri alınamaz.",
    );
  });

  it("vault export states the filesystem write", () => {
    expect(rendererFor("vault_export").consequence(approvalRequest(), "approve")).toContain(
      "Obsidian vault'a",
    );
  });
});

describe("summaries", () => {
  it("Gate B: first 20 changed files, a remainder count, tests and review", () => {
    const files = Array.from({ length: 25 }, (_, i) => `src/file${i}.ts`);
    renderSummary(
      approvalRequest({
        tool_name: "harness_push",
        display_summary: pushSummary({ changed_files: files, changed_files_total: 30 }),
      }),
    );
    expect(screen.getByText("Değişen dosyalar (30)")).toBeInTheDocument();
    expect(screen.getByText("src/file19.ts")).toBeInTheDocument();
    expect(screen.queryByText("src/file20.ts")).not.toBeInTheDocument();
    expect(screen.getByText("+10 daha")).toBeInTheDocument();
    expect(screen.getByText(/Model tarafından üretilmiş özet/)).toBeInTheDocument();
    expect(screen.getByText(/Geçti \(çıkış kodu 0\)/)).toBeInTheDocument();
    expect(screen.getByText("Looks correct.")).toBeInTheDocument();
    expect(screen.getByText("0123456789ab")).toBeInTheDocument();
  });

  it("purge lists the named titles and the remainder", () => {
    renderSummary(
      approvalRequest({
        tool_name: "purge_trash",
        display_summary: {
          kind: "purge_trash",
          task_count: 11,
          document_count: 1,
          titles: ["Süt al", "Rapor"],
        },
      }),
    );
    expect(screen.getByText("Kalıcı olarak silinecek: 11 görev, 1 belge")).toBeInTheDocument();
    expect(screen.getByText("Süt al")).toBeInTheDocument();
    expect(screen.getByText("+10 daha")).toBeInTheDocument();
  });

  it.each(["harness_implement", "harness_push", "purge_trash"])(
    "%s without a summary (non-curator) says so instead of crashing",
    (tool) => {
      renderSummary(approvalRequest({ tool_name: tool, display_summary: null }));
      expect(screen.getByText("Bu istek için özet bilgisi yok.")).toBeInTheDocument();
    },
  );

  it("never renders a summary of another tool's kind", () => {
    renderSummary(
      approvalRequest({ tool_name: "harness_implement", display_summary: pushSummary() }),
    );
    expect(screen.getByText("Bu istek için özet bilgisi yok.")).toBeInTheDocument();
    expect(screen.queryByText("Adds a /health endpoint.")).not.toBeInTheDocument();
  });
});
