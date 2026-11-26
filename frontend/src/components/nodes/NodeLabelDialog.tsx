"use client";

import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api-client";
import { parseAliases, updateNodeLabels } from "@/lib/api/home";
import type { ApiNodeDetail, ApiNodeRole, ApiRoom } from "@/types/api";

export const ROLE_LABELS: Record<ApiNodeRole, string> = {
  workstation: "Masaüstü iş istasyonu",
  laptop: "Dizüstü",
  media: "Medya bilgisayarı",
  server: "Sunucu",
  other: "Diğer",
};

export const PLATFORM_LABELS: Record<string, string> = {
  macos: "macOS",
  linux: "Linux",
  windows: "Windows",
};

/**
 * `/devices` node edit dialog (Phase 13 D3): the owner's name, room, role
 * and aliases for one node. Labels are server-side display and
 * resolution data; they are never sent to the node and never change its
 * permissions. The platform is shown, never edited (set at pairing).
 */
export function NodeLabelDialog({
  node,
  rooms,
  onSaved,
}: {
  node: ApiNodeDetail;
  rooms: ApiRoom[];
  onSaved: (node: ApiNodeDetail) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(node.name);
  const [roomId, setRoomId] = useState(node.room?.id ?? "");
  const [role, setRole] = useState<ApiNodeRole>(node.role ?? "other");
  const [aliases, setAliases] = useState((node.aliases ?? []).join(", "));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName(node.name);
    setRoomId(node.room?.id ?? "");
    setRole(node.role ?? "other");
    setAliases((node.aliases ?? []).join(", "));
    setError(null);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const updated = await updateNodeLabels(node.id, {
        name: name.trim(),
        room_id: roomId || null,
        role,
        aliases: parseAliases(aliases),
      });
      onSaved(updated);
      setOpen(false);
    } catch (err) {
      setError(errorMessage(err, "Etiketler kaydedilemedi."));
    } finally {
      setBusy(false);
    }
  }

  const prefix = `node-label-${node.id}`;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) reset();
        setOpen(next);
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          Düzenle
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Düğümü etiketle</DialogTitle>
          <DialogDescription>
            Ad, oda ve takma adlar Friday&apos;in &quot;hangi bilgisayar&quot; sorusunu çözmesi
            içindir; düğüme gönderilmez ve izinlerini değiştirmez. Platform:{" "}
            {PLATFORM_LABELS[node.platform ?? "macos"] ?? node.platform}.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <div className="flex flex-col gap-1">
            <Label htmlFor={`${prefix}-name`}>Ad</Label>
            <Input
              id={`${prefix}-name`}
              value={name}
              maxLength={100}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor={`${prefix}-room`}>Oda</Label>
            <select
              id={`${prefix}-room`}
              className="h-9 rounded-md border bg-background px-2 text-sm"
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              <option value="">Oda yok</option>
              {rooms.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor={`${prefix}-role`}>Rol</Label>
            <select
              id={`${prefix}-role`}
              className="h-9 rounded-md border bg-background px-2 text-sm"
              value={role}
              onChange={(event) => setRole(event.target.value as ApiNodeRole)}
            >
              {(Object.keys(ROLE_LABELS) as ApiNodeRole[]).map((value) => (
                <option key={value} value={value}>
                  {ROLE_LABELS[value]}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor={`${prefix}-aliases`}>Takma adlar (virgülle, en fazla 5)</Label>
            <Input
              id={`${prefix}-aliases`}
              value={aliases}
              onChange={(event) => setAliases(event.target.value)}
            />
          </div>
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setOpen(false)} disabled={busy}>
              Vazgeç
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? "Kaydediliyor…" : "Kaydet"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
