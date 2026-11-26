"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { useWorkspace } from "@/contexts/workspace-context";
import { errorMessage } from "@/lib/api-client";
import { createRoom, deleteRoom, fetchRooms, parseAliases, updateRoom } from "@/lib/api/home";
import { cn } from "@/lib/utils";
import type { ApiRoom, ApiRoomList } from "@/types/api";

interface Draft {
  name: string;
  aliases: string;
}

const toDraft = (room: ApiRoom): Draft => ({ name: room.name, aliases: room.aliases.join(", ") });

function RoomRow({
  room,
  index,
  count,
  busy,
  onSave,
  onMove,
  onDelete,
}: {
  room: ApiRoom;
  index: number;
  count: number;
  busy: boolean;
  onSave: (room: ApiRoom, draft: Draft) => Promise<void>;
  onMove: (index: number, delta: -1 | 1) => Promise<void>;
  onDelete: (room: ApiRoom) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(room));
  const dirty = draft.name !== room.name || draft.aliases !== room.aliases.join(", ");
  const nameId = `room-name-${room.id}`;
  const aliasesId = `room-aliases-${room.id}`;
  return (
    <li className="flex flex-col gap-2 rounded-lg border p-3" data-testid="room-row">
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor={nameId}>Oda adı</Label>
          <Input
            id={nameId}
            value={draft.name}
            maxLength={60}
            onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={aliasesId}>Takma adlar (virgülle, en fazla 5)</Label>
          <Input
            id={aliasesId}
            value={draft.aliases}
            onChange={(event) => setDraft((prev) => ({ ...prev, aliases: event.target.value }))}
          />
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={busy || !dirty} onClick={() => void onSave(room, draft)}>
          Kaydet
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={busy || index === 0}
          onClick={() => void onMove(index, -1)}
          aria-label={`${room.name} odasını yukarı taşı`}
        >
          Yukarı
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={busy || index === count - 1}
          onClick={() => void onMove(index, 1)}
          aria-label={`${room.name} odasını aşağı taşı`}
        >
          Aşağı
        </Button>
        <ConfirmDialog
          trigger={
            <Button size="sm" variant="outline" disabled={busy}>
              Sil
            </Button>
          }
          title="Oda silinsin mi?"
          description={`"${room.name}" silinecek. Bir düğüme atanmış oda silinemez.`}
          confirmLabel="Sil"
          busyLabel="Siliniyor…"
          errorFallback="Oda silinemedi."
          onConfirm={() => onDelete(room)}
        />
      </div>
    </li>
  );
}

/**
 * The rooms of the owner's personal workspace (Phase 13 D2): name, up to
 * five aliases and the display order. Rooms are labels Friday uses to
 * understand "salondaki"; they never change what a node may do.
 */
export function RoomsEditor({ workspaceId }: { workspaceId: string }) {
  const [data, setData] = useState<ApiRoomList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("");
  const [newAliases, setNewAliases] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await fetchRooms(workspaceId));
    } catch (err) {
      setError(errorMessage(err, "Odalar yüklenemedi."));
    }
  }, [workspaceId]);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount */
  useEffect(() => {
    void load();
  }, [load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function run(action: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
      return true;
    } catch (err) {
      setError(errorMessage(err, fallback));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add() {
    const name = newName.trim();
    if (!name) return;
    const ok = await run(
      () => createRoom(workspaceId, { name, aliases: parseAliases(newAliases) }),
      "Oda eklenemedi.",
    );
    if (ok) {
      setNewName("");
      setNewAliases("");
    }
  }

  async function save(room: ApiRoom, draft: Draft) {
    await run(
      () => updateRoom(workspaceId, room.id, { name: draft.name, aliases: parseAliases(draft.aliases) }),
      "Oda kaydedilemedi.",
    );
  }

  async function move(index: number, delta: -1 | 1) {
    const list = data?.results ?? [];
    const a = list[index];
    const b = list[index + delta];
    if (!a || !b) return;
    await run(async () => {
      await updateRoom(workspaceId, a.id, { sort_order: index + delta });
      await updateRoom(workspaceId, b.id, { sort_order: index });
    }, "Sıra değiştirilemedi.");
  }

  async function remove(room: ApiRoom) {
    // Errors surface in the confirmation dialog (`errorFallback`).
    await deleteRoom(workspaceId, room.id);
    await load();
  }

  const rooms = data?.results ?? [];

  return (
    <section className="flex flex-col gap-3" aria-labelledby="rooms-heading" data-testid="rooms-editor">
      <h2 id="rooms-heading" className="text-lg font-semibold tracking-tight">
        Odalar
      </h2>
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {data === null && !error ? <p className="text-sm text-muted-foreground">Yükleniyor…</p> : null}
      {data !== null && rooms.length === 0 ? (
        <p className="text-sm text-muted-foreground">Henüz oda yok.</p>
      ) : null}
      <ul className="flex flex-col gap-2" aria-label="Odalar">
        {rooms.map((room, index) => (
          <RoomRow
            key={`${room.id}:${room.updated_at}`}
            room={room}
            index={index}
            count={rooms.length}
            busy={busy}
            onSave={save}
            onMove={move}
            onDelete={remove}
          />
        ))}
      </ul>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Oda ekle</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
            onSubmit={(event) => {
              event.preventDefault();
              void add();
            }}
          >
            <div className="flex flex-col gap-1">
              <Label htmlFor="room-new-name">Oda adı</Label>
              <Input
                id="room-new-name"
                value={newName}
                maxLength={60}
                placeholder="Salon"
                onChange={(event) => setNewName(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="room-new-aliases">Takma adlar (virgülle)</Label>
              <Input
                id="room-new-aliases"
                value={newAliases}
                placeholder="oturma odası"
                onChange={(event) => setNewAliases(event.target.value)}
              />
            </div>
            <Button type="submit" disabled={busy || !newName.trim()}>
              Ekle
            </Button>
          </form>
        </CardContent>
      </Card>
    </section>
  );
}

/** `/settings/home`: the home's rooms (personal workspace only, Phase 13 D2). */
export function HomeSettings() {
  const { workspaces } = useWorkspace();
  const personal = workspaces.find((workspace) => workspace.type === "personal") ?? null;
  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-6")} data-testid="home-settings">
      <PageHeader
        title="Ev"
        description={
          "Odalar, Friday'in \"salondaki\" ya da \"çalışma odasındaki\" gibi ifadeleri anlaması içindir. " +
          "Bilgisayarlarını /devices sayfasında bir odaya atarsın. Odalar yalnızca kişisel alanındadır ve " +
          "hiçbir düğümün yetkisini değiştirmez."
        }
      />
      {personal ? (
        <RoomsEditor workspaceId={personal.id} />
      ) : (
        <p className="text-sm text-muted-foreground">Kişisel çalışma alanı bulunamadı.</p>
      )}
    </div>
  );
}
