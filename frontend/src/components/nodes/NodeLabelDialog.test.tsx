import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NodeLabelDialog } from "@/components/nodes/NodeLabelDialog";
import type { ApiNodeDetail, ApiRoom } from "@/types/api";

const ID = "33333333-3333-4333-8333-333333333333";

const NODE: ApiNodeDetail = {
  id: ID,
  name: "MacBook",
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
  advertised_count: 1,
  invocable_count: 1,
  snapshot_at: null,
  capabilities: [],
  name_key: "macbook",
  room: null,
  role: "other",
  platform: "linux",
  aliases: [],
  last_active_at: null,
};

const ROOMS: ApiRoom[] = [
  {
    id: "r1",
    name: "Çalışma odası",
    key: "çalişma odasi",
    aliases: [],
    sort_order: 0,
    created_at: "2026-11-20T10:00:00Z",
    updated_at: "2026-11-20T10:00:00Z",
  },
];

describe("NodeLabelDialog (Phase 13 D3)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends name, room, role and aliases -- never the platform", async () => {
    const saved = { ...NODE, name: "Masaüstü", role: "workstation" as const };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(saved), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(<NodeLabelDialog node={NODE} rooms={ROOMS} onSaved={onSaved} />);
    await user.click(screen.getByRole("button", { name: "Düzenle" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Platform: Linux/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/Platform/)).toBeNull();
    const name = within(dialog).getByLabelText("Ad");
    await user.clear(name);
    await user.type(name, "Masaüstü");
    await user.selectOptions(within(dialog).getByLabelText("Oda"), "r1");
    await user.selectOptions(within(dialog).getByLabelText("Rol"), "workstation");
    await user.type(within(dialog).getByLabelText(/Takma adlar/), "masa, iş bilgisayarı");
    await user.click(within(dialog).getByRole("button", { name: "Kaydet" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    const [url, init] = fetchMock.mock.calls[0]! as unknown as [string, RequestInit];
    expect(url).toBe(`/api/nodes/${ID}`);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({
      name: "Masaüstü",
      room_id: "r1",
      role: "workstation",
      aliases: ["masa", "iş bilgisayarı"],
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("clears the room with 'Oda yok' and shows a conflict in Turkish", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ detail: "x", code: "label.conflict" }), { status: 409 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(
      <NodeLabelDialog
        node={{ ...NODE, room: { id: "r1", name: "Çalışma odası", key: "x" } }}
        rooms={ROOMS}
        onSaved={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Düzenle" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Oda")).toHaveValue("r1");
    await user.selectOptions(within(dialog).getByLabelText("Oda"), "");
    await user.click(within(dialog).getByRole("button", { name: "Kaydet" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("zaten kullanılıyor");
    const [, init] = fetchMock.mock.calls[0]! as unknown as [string, RequestInit];
    expect(JSON.parse(init.body as string).room_id).toBeNull();
  });
});
