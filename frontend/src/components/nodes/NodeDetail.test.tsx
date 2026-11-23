import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NodeDetailPage } from "@/components/nodes/NodeDetail";
import type { ApiNodeDetail, ApiNodeInvocation } from "@/types/api";

const ID = "33333333-3333-4333-8333-333333333333";
const ARGS = "a".repeat(64);
const OUT = "b".repeat(64);

const NODE: ApiNodeDetail = {
  id: ID,
  name: "MacBook Pro",
  status: "disconnected",
  online: false,
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
  advertised_count: 1,
  invocable_count: 1,
  snapshot_at: null,
  capabilities: [
    { name: "sessions.list", version: 1, class: "read", availability: "granted", invocable: true, reason: null },
  ],
};

function row(seq: number, overrides: Partial<ApiNodeInvocation> = {}): ApiNodeInvocation {
  return {
    id: `row-${seq}`,
    seq,
    created_at: "2026-11-20T10:00:00Z",
    capability: "sessions.list",
    capability_version: 1,
    class: "read",
    status: "ok",
    refusal_code: "",
    args_sha256: ARGS,
    output_sha256: OUT,
    output_bytes: 1234,
    truncated: false,
    dropped_unmapped: 2,
    duration_ms: 87,
    channel: "web",
    trace_id: "t",
    workspace_id: "w",
    workspace_name: "Kişisel",
    row_hash: "c".repeat(64),
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("NodeDetailPage (Phase 9 D11)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows hashes, status and sizes, never content, and pages older rows", async () => {
    const firstPage = Array.from({ length: 2 }, (_, i) => row(60 - i));
    firstPage.push(
      row(58, {
        status: "server_refused",
        refusal_code: "path_not_in_workspace",
        output_sha256: "",
        output_bytes: 0,
        dropped_unmapped: 0,
        // Even if a backend ever sent extra fields, the view renders only
        // the ones it names.
        ...({ output: "SECRET TRANSCRIPT TEXT", args: { path: "~/secret" } } as object),
      }),
    );
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/nodes/${ID}`) return jsonResponse(NODE);
      if (url === `/api/nodes/${ID}/invocations`) return jsonResponse({ results: firstPage, next_cursor: 58 });
      if (url === `/api/nodes/${ID}/invocations?cursor=58`) {
        return jsonResponse({ results: [row(57, { status: "node_offline", output_sha256: "" })], next_cursor: null });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NodeDetailPage nodeId={ID} />);

    const log = await screen.findByTestId("node-invocation-log");
    const rows = within(log).getAllByTestId("node-invocation");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent(ARGS.slice(0, 12));
    expect(rows[0]).toHaveTextContent(OUT.slice(0, 12));
    expect(rows[0]).toHaveTextContent("1234 bayt");
    expect(rows[0]).toHaveTextContent("2 eşleşmeyen satır atıldı");
    expect(rows[0]).toHaveTextContent("tamam");
    expect(rows[0]).toHaveTextContent("87 ms");
    expect(rows[0]).toHaveTextContent("Kişisel");
    expect(rows[2]).toHaveTextContent("sunucu reddetti · path_not_in_workspace");
    expect(document.body).not.toHaveTextContent("SECRET TRANSCRIPT TEXT");
    expect(document.body).not.toHaveTextContent("~/secret");
    expect(screen.getByText("MacBook Pro")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Daha eski çağrılar" }));
    await waitFor(() => expect(within(log).getAllByTestId("node-invocation")).toHaveLength(4));
    expect(within(log).getAllByTestId("node-invocation")[3]).toHaveTextContent("Mac'e ulaşılamadı");
    expect(screen.queryByRole("button", { name: "Daha eski çağrılar" })).not.toBeInTheDocument();
  });

  it("shows an empty log and a failed load", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.endsWith("/invocations") ? jsonResponse({ results: [], next_cursor: null }) : jsonResponse(NODE),
      ),
    );
    const view = render(<NodeDetailPage nodeId={ID} />);
    expect(await screen.findByText("Henüz çağrı yok.")).toBeInTheDocument();
    view.unmount();

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "Not found.", code: "not_found" }, 404)));
    render(<NodeDetailPage nodeId={ID} />);
    expect(await screen.findByText("Düğüm yüklenemedi.")).toBeInTheDocument();
  });
});
