import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { RENDERED_TOOL_NAMES, rendererFor } from "@/components/approvals/renderers";
import { genericConnectorWriteRenderer } from "@/components/approvals/renderers/connector-write";
import { genericRenderer } from "@/components/approvals/renderers/generic";
import { approvalRequest } from "@/components/approvals/test-fixtures";
import { RUN_ID, SHA_LIVE, SHA_NEW, deploySummary, writeSummary } from "@/components/deploys/test-fixtures";
import { POLL_FAST_MS } from "@/lib/query/polling";
import type { ApiApprovalRequest } from "@/types/api";

function renderRequest(request: ApiApprovalRequest) {
  const { Summary, Execution } = rendererFor(request.tool_name);
  return render(
    <>
      <Summary request={request} />
      <Execution request={request} />
    </>,
  );
}

const CONNECTOR_GATES = ["github_open_pr", "ha_call_service", "mail_send", "calendar_create_event"];

describe("renderer selection (Phase 5)", () => {
  it.each(["deploy", ...CONNECTOR_GATES])("selects the dedicated renderer for %s", (tool) => {
    expect(rendererFor(tool).toolName).toBe(tool);
    expect(RENDERED_TOOL_NAMES).toContain(tool);
  });

  it.each(["mcp_fake_create_note", "mcp_notion_create_page"])(
    "a generic connector gate %s gets the connector-write renderer",
    (tool) => {
      expect(rendererFor(tool)).toBe(genericConnectorWriteRenderer);
      expect(RENDERED_TOOL_NAMES).not.toContain(tool);
    },
  );

  it.each(["mcp_", "mcp", "deployx", "MCP_x"])("%j stays on the generic fallback", (tool) => {
    expect(rendererFor(tool)).toBe(genericRenderer);
  });
});

describe("deploy renderer", () => {
  it("renders the whole decided plan from the snapshot", () => {
    renderRequest(approvalRequest({ tool_name: "deploy", display_summary: deploySummary() }));
    const summary = screen.getByTestId("deploy-summary");
    // Full SHAs (new, live, rollback), never only an abbreviation.
    expect(within(summary).getAllByText(SHA_NEW)).toHaveLength(1);
    expect(within(summary).getAllByText(SHA_LIVE).length).toBe(2);
    expect(summary).toHaveTextContent("deploy@203.0.113.7:2222");
    expect(summary).toHaveTextContent("adia-web");
    expect(summary).toHaveTextContent("git@github.com:acme/adia-web.git");
    expect(summary).toHaveTextContent("Commit başlığı (depodan gelen metin)");
    expect(summary).toHaveTextContent("Ship the header");
    expect(summary).toHaveTextContent("CI başarılı");
    expect(summary).toHaveTextContent("120 sn içinde 4 kontrol");
    expect(summary).toHaveTextContent("https://adia.example/healthz");
    expect(summary).toHaveTextContent("status → deploy → health → rollback → health");
  });

  it("says plainly when nothing will be rolled back", () => {
    renderRequest(
      approvalRequest({
        tool_name: "deploy",
        display_summary: deploySummary({ auto_rollback: false, verbs: ["status", "deploy", "health"] }),
      }),
    );
    expect(screen.getByText(/başarısızlıkta hiçbir şey geri alınmaz/)).toBeInTheDocument();
    const request = approvalRequest({
      tool_name: "deploy",
      display_summary: deploySummary({ auto_rollback: false }),
    });
    expect(rendererFor("deploy").consequence(request, "approve")).toContain("hiçbir şey geri alınmaz");
  });

  it("the consequence names the SHA, target and rollback SHA", () => {
    const request = approvalRequest({ tool_name: "deploy", display_summary: deploySummary() });
    const text = rendererFor("deploy").consequence(request, "approve");
    expect(text).toContain(SHA_NEW.slice(0, 12));
    expect(text).toContain("ADİA (production)");
    expect(text).toContain(SHA_LIVE.slice(0, 12));
    expect(rendererFor("deploy").consequence(request, "reject")).toContain("hiçbir şey dağıtılmaz");
  });

  it("never renders another tool's summary kind", () => {
    renderRequest(approvalRequest({ tool_name: "deploy", display_summary: writeSummary() }));
    expect(screen.getByText("Bu istek için özet bilgisi yok.")).toBeInTheDocument();
  });

  it.each([
    ["rolled_back", /Geri alındı/],
    ["rollback_failed", /Geri alma BAŞARISIZ/],
    ["health_failed", /Sağlık kontrolü başarısız: yeni commit canlıda/],
    ["succeeded_degraded", /Kısmen başarılı — temiz değil/],
    ["unknown_outcome", /Sonuç bilinmiyor: çalıştırıcıyla bağlantı/],
  ])("the execution shows the %s banner and a run link", (status, text) => {
    renderRequest(
      approvalRequest({
        tool_name: "deploy",
        status: "approved",
        display_summary: deploySummary(),
        execution: {
          run_id: RUN_ID,
          status: status as never,
          error_code: "",
          active: false,
          needs_attention: true,
          deploy_started: true,
          rollback_attempted: status.startsWith("roll"),
          health_checks: 3,
          finished_at: "2026-11-20T00:00:00Z",
        },
      }),
    );
    expect(screen.getByTestId(`deploy-banner-${status}`)).toHaveTextContent(text);
    expect(screen.getByRole("link", { name: "Çalıştırmayı aç" })).toHaveAttribute(
      "href",
      `/deploys/runs/${RUN_ID}?workspace=w1`,
    );
  });

  it("polls while the run is active and stops once terminal", () => {
    const base = { run_id: RUN_ID, error_code: "", needs_attention: false, deploy_started: true, rollback_attempted: false, health_checks: 0, finished_at: null };
    const running = approvalRequest({
      tool_name: "deploy",
      execution: { ...base, status: "health_checking", active: true },
    });
    const done = approvalRequest({
      tool_name: "deploy",
      execution: { ...base, status: "succeeded", active: false },
    });
    expect(rendererFor("deploy").pollInterval(running)).toBe(POLL_FAST_MS);
    expect(rendererFor("deploy").pollInterval(done)).toBeNull();
  });
});

describe("connector write renderers", () => {
  it("mail_send shows raw addresses, the whole body and the exact JSON sent", () => {
    const summary = writeSummary();
    renderRequest(approvalRequest({ tool_name: "mail_send", display_summary: summary }));
    const card = screen.getByTestId("connector-write-summary");
    expect(card).toHaveTextContent("Ayşe <ayse@example.com>, bob@example.org");
    // The body is shown in full, never truncated.
    expect(card.textContent).toContain(summary.rows[2]!.value);
    expect(screen.getByTestId("connector-write-arguments").textContent).toBe(summary.arguments_json);
    expect(card).toHaveTextContent("c".repeat(64));
    expect(card).toHaveTextContent("Kişisel veri");
  });

  it("ha_call_service: the consequence names the exact service", () => {
    const request = approvalRequest({
      tool_name: "ha_call_service",
      display_summary: writeSummary({
        connector: "home_assistant",
        tool: "ha_call_service",
        rows: [
          { label: "Hizmet", value: "light.turn_on" },
          { label: "Varlık (entity_id)", value: "light.salon" },
          { label: "data.brightness_pct", value: "40" },
        ],
      }),
    });
    expect(rendererFor("ha_call_service").consequence(request, "approve")).toContain("“light.turn_on”");
    renderRequest(request);
    expect(screen.getByText("light.salon")).toBeInTheDocument();
    expect(screen.getByText("data.brightness_pct")).toBeInTheDocument();
  });

  it("github_open_pr: the consequence names repo, head and base; context rows are shown", () => {
    const request = approvalRequest({
      tool_name: "github_open_pr",
      display_summary: writeSummary({
        connector: "github",
        tool: "github_open_pr",
        data_class: "firm",
        rows: [
          { label: "Depo", value: "acme/adia-web" },
          { label: "Dal (head)", value: "harness/p3-ij1" },
          { label: "Hedef dal (base)", value: "main" },
          { label: "İnceleyiciler (sabit)", value: "(yok)" },
        ],
        context_rows: [{ label: "head_sha", value: SHA_NEW }],
      }),
    });
    const text = rendererFor("github_open_pr").consequence(request, "approve");
    expect(text).toContain("acme/adia-web");
    expect(text).toContain("“harness/p3-ij1” → “main”");
    renderRequest(request);
    expect(screen.getByText("head_sha (kontrol)")).toBeInTheDocument();
    expect(screen.getByText(SHA_NEW)).toBeInTheDocument();
    expect(screen.queryByText("Kişisel veri")).not.toBeInTheDocument();
  });

  it("calendar_create_event states that invitations are sent", () => {
    expect(
      rendererFor("calendar_create_event").consequence(approvalRequest({ tool_name: "calendar_create_event" }), "approve"),
    ).toContain("davet e-postası");
  });

  it("a generic mcp_ write is titled from its snapshot and lists every argument row", () => {
    const request = approvalRequest({
      tool_name: "mcp_fake_create_note",
      display_summary: writeSummary({
        connector: "fake",
        connector_name: "Fake",
        tool: "fake_create_note",
        rows: [{ label: "title", value: "Hello" }],
      }),
    });
    const renderer = rendererFor(request.tool_name);
    expect(renderer.title(request)).toBe("Fake yazma onayı (fake_create_note)");
    expect(renderer.consequence(request, "approve")).toContain("yeniden gönderilmez");
    renderRequest(request);
    expect(screen.getByText("Hello")).toBeInTheDocument();
  });

  it.each([
    ["unknown_outcome", "Sonuç bilinmiyor — yeniden gönderilmez", "worker_lost"],
    ["failed", "Başarısız — hiçbir şey gönderilmedi", "connector_changed"],
    ["succeeded", "Gönderildi", ""],
  ])("execution %s is labelled and explained", (status, label, errorType) => {
    renderRequest(
      approvalRequest({
        tool_name: "mail_send",
        status: "approved",
        display_summary: writeSummary(),
        execution: { status: status as never, error_type: errorType, started_at: null, finished_at: null },
      }),
    );
    expect(screen.getByText(label)).toBeInTheDocument();
    if (status === "unknown_outcome") {
      expect(screen.getByRole("alert")).toHaveTextContent("asla yeniden göndermez");
    } else {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    }
  });

  it("without a summary (non-curator) says so", () => {
    renderRequest(approvalRequest({ tool_name: "mail_send", display_summary: null }));
    expect(screen.getByText("Bu istek için özet bilgisi yok.")).toBeInTheDocument();
  });
});
