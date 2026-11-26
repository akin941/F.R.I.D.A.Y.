import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NodeAuditLog, NodeDetailPage } from "@/components/nodes/NodeDetail";
import type { ApiNodeAuditRow, ApiNodeDetail } from "@/types/api";

const ID = "33333333-3333-4333-8333-333333333333";

const NODE: ApiNodeDetail = {
  id: ID,
  name: "Masaüstü",
  status: "connected",
  online: true,
  protocol_version: 1,
  node_version: "0.1.0",
  os_version: "",
  last_heartbeat_at: null,
  spool_count: 0,
  spool_bytes: 0,
  paired_at: "2026-11-01T10:00:00Z",
  revoked_at: null,
  credential_age_days: 2,
  credential_expires_at: null,
  rotate_after_days: 30,
  pinned_key_id: "",
  catalogue_sha256: "0123456789ab",
  advertised_count: 0,
  invocable_count: 0,
  snapshot_at: null,
  capabilities: [],
};

const EVENT: ApiNodeAuditRow = {
  kind: "event",
  id: "e1",
  created_at: "2026-11-20T10:00:00Z",
  action: "nodes.relabelled",
  actor_type: "human",
  details: { old_key: "macbook", new_key: "masaüstü", fields: ["name"] },
};

const INVOCATION: ApiNodeAuditRow = {
  kind: "invocation",
  id: "i1",
  seq: 7,
  created_at: "2026-11-20T09:00:00Z",
  capability: "git.status",
  capability_version: 1,
  class: "read",
  status: "server_refused",
  refusal_code: "path_not_in_workspace",
  args_sha256: "a".repeat(64),
  output_sha256: "",
  output_bytes: 0,
  truncated: false,
  dropped_unmapped: 0,
  duration_ms: 0,
  channel: "web",
  trace_id: "t",
  workspace_id: "w",
  workspace_name: "Kişisel",
  row_hash: "c".repeat(64),
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("node audit (Phase 13 D8)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders events with their keys and chain rows with their status", () => {
    render(<NodeAuditLog rows={[EVENT, INVOCATION]} />);
    const event = screen.getByTestId("node-audit-event");
    expect(within(event).getByText("Etiketler değişti")).toBeInTheDocument();
    expect(event).toHaveTextContent("new_key: masaüstü");
    const invocation = screen.getByTestId("node-audit-invocation");
    expect(invocation).toHaveTextContent("git.status");
    expect(invocation).toHaveTextContent("path_not_in_workspace");
  });

  it("the Denetim tab loads the audit and pages with the cursor", async () => {
    const cursor = `1764000000000000.${ID}`;
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/nodes/${ID}`) return jsonResponse(NODE);
      if (url === `/api/nodes/${ID}/invocations`) return jsonResponse({ results: [], next_cursor: null });
      if (url === `/api/nodes/${ID}/audit`) return jsonResponse({ results: [EVENT], next_cursor: cursor });
      if (url === `/api/nodes/${ID}/audit?before=${encodeURIComponent(cursor)}`)
        return jsonResponse({ results: [INVOCATION], next_cursor: null });
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<NodeDetailPage nodeId={ID} />);
    await screen.findByRole("heading", { name: /Masaüstü/ });
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/audit"))).toBe(false);
    await user.click(screen.getByRole("tab", { name: "Denetim" }));
    expect(await screen.findByTestId("node-audit-event")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Daha/ }));
    expect(await screen.findByTestId("node-audit-invocation")).toBeInTheDocument();
    expect(screen.getAllByTestId(/node-audit-(event|invocation)/)).toHaveLength(2);
  });
});
