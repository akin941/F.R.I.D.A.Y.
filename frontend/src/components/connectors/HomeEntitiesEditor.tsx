"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api-client";
import {
  createHomeEntity,
  deleteHomeEntity,
  fetchHomeEntities,
  fetchHomeEntityCandidates,
  fetchRooms,
  parseAliases,
  updateHomeEntity,
} from "@/lib/api/home";
import { TONE_PILL_CLASSES } from "@/lib/theme/status";
import type {
  ApiHomeEntity,
  ApiHomeEntityCandidate,
  ApiHomeEntityList,
  ApiRoom,
} from "@/types/api";

/** Turkish names of the code-maximum services (the code is always shown too). */
export const SERVICE_LABELS: Record<string, string> = {
  turn_on: "aç",
  turn_off: "kapat",
  media_play: "oynat",
  media_pause: "duraklat",
  // Phase 13b D4/D7: the toggle's card shows the live state and the outcome
  // it implies. Voice "durdur" pauses (owner 2026-11-27); English "stop" is
  // media_stop, still labelled "durdur" here as its Turkish name.
  media_play_pause: "oynat/duraklat",
  media_stop: "durdur",
  select_source: "kaynak seç",
  volume_set: "ses düzeyi",
  volume_mute: "sessiz",
  volume_up: "sesi aç",
  volume_down: "sesi kıs",
  set_percentage: "hız",
  open_cover: "aç",
  close_cover: "kapat",
  stop_cover: "durdur",
  set_cover_position: "konum",
  set_temperature: "sıcaklık",
  set_hvac_mode: "mod",
};

function ServiceBoxes({
  idPrefix,
  options,
  selected,
  onChange,
}: {
  idPrefix: string;
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  if (options.length === 0) {
    return <p className="text-xs text-muted-foreground">Bu alan için izin verilebilecek hizmet yok.</p>;
  }
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="text-sm font-medium">İzin verilen hizmetler</legend>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {options.map((service) => {
          const id = `${idPrefix}-${service}`;
          return (
            <label key={service} htmlFor={id} className="flex items-center gap-1.5 text-sm">
              <input
                id={id}
                type="checkbox"
                checked={selected.includes(service)}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...selected, service]
                      : selected.filter((s) => s !== service),
                  )
                }
              />
              <span>{SERVICE_LABELS[service] ?? service}</span>
              <code className="font-mono text-xs text-muted-foreground">{service}</code>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

/**
 * Phase 13b D5/D11 (ADR-018): why an entity cannot be flagged "direct", or
 * null when it can. Only a `media_player` with at least one ticked service
 * of the closed class is eligible (the backend refuses anything else).
 */
export function directIneligibleReason(entity: ApiHomeEntity): string | null {
  if (entity.domain !== "media_player") return "Yalnızca medya oynatıcılar doğrudan olabilir.";
  if ((entity.direct_services ?? []).length === 0) {
    return "Doğrudan sınıftan izinli hizmet yok (oynat, duraklat, durdur, ses).";
  }
  return null;
}

function DirectSwitch({
  entity,
  busy,
  onChange,
}: {
  entity: ApiHomeEntity;
  busy: boolean;
  onChange: (entity: ApiHomeEntity, direct: boolean) => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const id = `ha-entity-${entity.id}-direct`;
  const reason = directIneligibleReason(entity);
  const on = Boolean(entity.direct);
  const covered = entity.direct_services ?? [];
  return (
    <div className="flex flex-col gap-1" data-testid="ha-direct">
      <label htmlFor={id} className="flex items-center gap-2 text-sm font-medium">
        <input
          id={id}
          type="checkbox"
          role="switch"
          aria-checked={on}
          checked={on}
          disabled={busy || reason !== null}
          aria-describedby={`${id}-help`}
          onChange={() => {
            // Turning it on widens the one exception to the approval gate:
            // the owner confirms it. Turning it off is immediate.
            if (on) void onChange(entity, false);
            else setConfirming(true);
          }}
        />
        Doğrudan (onaysız)
        {on ? (
          <Badge variant="outline" className={TONE_PILL_CLASSES.warning}>
            Kartsız
          </Badge>
        ) : null}
      </label>
      <p id={`${id}-help`} className="text-xs text-muted-foreground" data-testid="ha-direct-help">
        {reason ??
          `Onay kartı olmadan çalışır: ${covered.map((service) => SERVICE_LABELS[service] ?? service).join(", ")}. Diğer her işlem yine onay bekler.`}
      </p>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Bu cihaz doğrudan olsun mu?"
        description={`"${entity.entity_id}" için ${covered.join(", ")} komutları sesli ya da yazılı isteğinde onay kartı olmadan çalışır. Dakikalık sınırlar, ses üst sınırı ve "Doğrudan medyayı kapat" anahtarı geçerlidir.`}
        confirmLabel="Doğrudan yap"
        busyLabel="Kaydediliyor…"
        errorFallback="Doğrudan işareti kaydedilemedi."
        destructive={false}
        onConfirm={() => onChange(entity, true)}
      />
    </div>
  );
}

function RoomSelect({
  id,
  rooms,
  value,
  onChange,
}: {
  id: string;
  rooms: ApiRoom[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>Oda</Label>
      <select
        id={id}
        className="h-9 rounded-md border bg-background px-2 text-sm"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Oda yok</option>
        {rooms.map((room) => (
          <option key={room.id} value={room.id}>
            {room.name}
          </option>
        ))}
      </select>
    </div>
  );
}

interface Draft {
  label: string;
  roomId: string;
  aliases: string;
  services: string[];
}

const toDraft = (entity: ApiHomeEntity): Draft => ({
  label: entity.label,
  roomId: entity.room?.id ?? "",
  aliases: entity.aliases.join(", "),
  services: [...entity.services],
});

function sameSet(a: string[], b: string[]) {
  return a.length === b.length && a.every((value) => b.includes(value));
}

function EntityRow({
  entity,
  rooms,
  options,
  busy,
  onSave,
  onToggle,
  onDelete,
  onDirect,
}: {
  entity: ApiHomeEntity;
  rooms: ApiRoom[];
  options: string[];
  busy: boolean;
  onSave: (entity: ApiHomeEntity, draft: Draft) => Promise<void>;
  onToggle: (entity: ApiHomeEntity) => Promise<void>;
  onDelete: (entity: ApiHomeEntity) => Promise<void>;
  onDirect: (entity: ApiHomeEntity, direct: boolean) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(entity));
  const initial = toDraft(entity);
  const dirty =
    draft.label !== initial.label ||
    draft.roomId !== initial.roomId ||
    draft.aliases !== initial.aliases ||
    !sameSet(draft.services, initial.services);
  const prefix = `ha-entity-${entity.id}`;
  return (
    <li className="flex flex-col gap-2 rounded-lg border p-3" data-testid="ha-entity-row">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">
          {entity.room ? `${entity.room.name} · ` : ""}
          {entity.label}
        </span>
        <code className="font-mono text-xs break-all text-muted-foreground">{entity.entity_id}</code>
        {entity.enabled ? (
          <Badge variant="outline">Etkin</Badge>
        ) : (
          <Badge variant="outline" className={TONE_PILL_CLASSES.warning}>
            Devre dışı
          </Badge>
        )}
      </p>
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${prefix}-label`}>Etiket</Label>
          <Input
            id={`${prefix}-label`}
            value={draft.label}
            maxLength={60}
            onChange={(event) => setDraft((prev) => ({ ...prev, label: event.target.value }))}
          />
        </div>
        <RoomSelect
          id={`${prefix}-room`}
          rooms={rooms}
          value={draft.roomId}
          onChange={(roomId) => setDraft((prev) => ({ ...prev, roomId }))}
        />
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${prefix}-aliases`}>Takma adlar (virgülle, en fazla 5)</Label>
          <Input
            id={`${prefix}-aliases`}
            value={draft.aliases}
            onChange={(event) => setDraft((prev) => ({ ...prev, aliases: event.target.value }))}
          />
        </div>
      </div>
      <ServiceBoxes
        idPrefix={`${prefix}-svc`}
        options={options}
        selected={draft.services}
        onChange={(services) => setDraft((prev) => ({ ...prev, services }))}
      />
      <DirectSwitch entity={entity} busy={busy} onChange={onDirect} />
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={busy || !dirty || draft.services.length === 0}
          onClick={() => void onSave(entity, draft)}
        >
          Kaydet
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void onToggle(entity)}>
          {entity.enabled ? "Devre dışı bırak" : "Etkinleştir"}
        </Button>
        <ConfirmDialog
          trigger={
            <Button size="sm" variant="outline" disabled={busy}>
              Listeden çıkar
            </Button>
          }
          title="Varlık izin listesinden çıkarılsın mı?"
          description={`"${entity.entity_id}" artık hiçbir şekilde çağrılamaz; bekleyen onaylar da gönderilmez.`}
          confirmLabel="Çıkar"
          busyLabel="Çıkarılıyor…"
          errorFallback="Varlık çıkarılamadı."
          destructive
          onConfirm={() => onDelete(entity)}
        />
      </div>
    </li>
  );
}

function AddEntity({
  workspaceId,
  rooms,
  busy,
  onAdd,
}: {
  workspaceId: string;
  rooms: ApiRoom[];
  busy: boolean;
  onAdd: (entityId: string, draft: Draft) => Promise<boolean>;
}) {
  const [candidates, setCandidates] = useState<ApiHomeEntityCandidate[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [entityId, setEntityId] = useState("");
  const [draft, setDraft] = useState<Draft>({ label: "", roomId: "", aliases: "", services: [] });

  async function readCandidates() {
    setLoading(true);
    setLoadError(null);
    try {
      setCandidates((await fetchHomeEntityCandidates(workspaceId)).results);
    } catch (err) {
      setLoadError(errorMessage(err, "Home Assistant okunamadı."));
    } finally {
      setLoading(false);
    }
  }

  const available = (candidates ?? []).filter((candidate) => !candidate.allowlisted);
  const picked = available.find((candidate) => candidate.entity_id === entityId) ?? null;

  async function submit() {
    if (!picked) return;
    if (await onAdd(picked.entity_id, draft)) {
      setEntityId("");
      setDraft({ label: "", roomId: "", aliases: "", services: [] });
      setCandidates((prev) =>
        prev?.map((c) => (c.entity_id === picked.entity_id ? { ...c, allowlisted: true } : c)) ?? null,
      );
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Varlık ekle</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          Liste Home Assistant&apos;tan canlı okunur; bir varlık yalnızca sen ekleyince izin listesine girer.
        </p>
        <Button size="sm" variant="outline" className="self-start" disabled={loading} onClick={() => void readCandidates()}>
          {loading ? "Okunuyor…" : "Home Assistant'tan varlıkları oku"}
        </Button>
        {loadError ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{loadError}</AlertDescription>
          </Alert>
        ) : null}
        {candidates !== null && available.length === 0 ? (
          <p className="text-sm text-muted-foreground">Eklenebilecek yeni varlık yok.</p>
        ) : null}
        {available.length > 0 ? (
          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <div className="flex flex-col gap-1">
              <Label htmlFor="ha-new-entity">Varlık</Label>
              <select
                id="ha-new-entity"
                className="h-9 rounded-md border bg-background px-2 text-sm"
                value={entityId}
                onChange={(event) => {
                  setEntityId(event.target.value);
                  setDraft((prev) => ({ ...prev, services: [] }));
                }}
              >
                <option value="">Seç…</option>
                {available.map((candidate) => (
                  <option key={candidate.entity_id} value={candidate.entity_id}>
                    {candidate.entity_id}
                    {candidate.friendly_name ? ` (${candidate.friendly_name})` : ""}
                  </option>
                ))}
              </select>
            </div>
            {picked ? (
              <>
                <div className="grid gap-2 sm:grid-cols-3">
                  <div className="flex flex-col gap-1">
                    <Label htmlFor="ha-new-label">Etiket</Label>
                    <Input
                      id="ha-new-label"
                      value={draft.label}
                      maxLength={60}
                      placeholder="TV"
                      onChange={(event) => setDraft((prev) => ({ ...prev, label: event.target.value }))}
                    />
                  </div>
                  <RoomSelect
                    id="ha-new-room"
                    rooms={rooms}
                    value={draft.roomId}
                    onChange={(roomId) => setDraft((prev) => ({ ...prev, roomId }))}
                  />
                  <div className="flex flex-col gap-1">
                    <Label htmlFor="ha-new-aliases">Takma adlar (virgülle)</Label>
                    <Input
                      id="ha-new-aliases"
                      value={draft.aliases}
                      onChange={(event) => setDraft((prev) => ({ ...prev, aliases: event.target.value }))}
                    />
                  </div>
                </div>
                {picked.source_list.length > 0 ? (
                  <p className="text-xs text-muted-foreground">
                    Kaynaklar (Home Assistant&apos;tan gelen metin): {picked.source_list.join(", ")}
                  </p>
                ) : null}
                <ServiceBoxes
                  idPrefix="ha-new-svc"
                  options={picked.services}
                  selected={draft.services}
                  onChange={(services) => setDraft((prev) => ({ ...prev, services }))}
                />
                <Button
                  type="submit"
                  className="self-start"
                  disabled={busy || !draft.label.trim() || draft.services.length === 0}
                >
                  İzin listesine ekle
                </Button>
              </>
            ) : null}
          </form>
        ) : null}
      </CardContent>
    </Card>
  );
}

/**
 * The owner's Home Assistant entity allowlist (Phase 13 D11), on
 * `/connectors` under the Home Assistant card: only the entities listed
 * here, and only their ticked services, can ever be called -- each call
 * still needs its own approval on `/approvals`, except (Phase 13b, ADR-018)
 * the closed media services on a player the owner flagged "direct". The services offered are
 * the code maximum for the entity's domain (never a toggle, never a lock
 * or script). An empty list means nothing is callable.
 */
export function HomeEntitiesEditor({
  workspaceId,
  onDirectChanged,
}: {
  workspaceId: string;
  /** Phase 13b: a "direct" flag changed (the settings panel refreshes its count). */
  onDirectChanged?: () => void;
}) {
  const [data, setData] = useState<ApiHomeEntityList | null>(null);
  const [rooms, setRooms] = useState<ApiRoom[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await fetchHomeEntities(workspaceId));
    } catch (err) {
      setError(errorMessage(err, "İzin listesi yüklenemedi."));
    }
  }, [workspaceId]);

  const loadRooms = useCallback(async () => {
    try {
      setRooms((await fetchRooms(workspaceId)).results);
    } catch {
      setRooms([]);
    }
  }, [workspaceId]);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount */
  useEffect(() => {
    void load();
    void loadRooms();
  }, [load, loadRooms]);
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

  const add = (entityId: string, draft: Draft) =>
    run(
      () =>
        createHomeEntity(workspaceId, entityId, {
          label: draft.label.trim(),
          room_id: draft.roomId || null,
          aliases: parseAliases(draft.aliases),
          services: draft.services,
        }),
      "Varlık eklenemedi.",
    );

  async function save(entity: ApiHomeEntity, draft: Draft) {
    await run(
      () =>
        updateHomeEntity(entity.id, {
          label: draft.label,
          room_id: draft.roomId || null,
          aliases: parseAliases(draft.aliases),
          services: draft.services,
        }),
      "Varlık kaydedilemedi.",
    );
  }

  async function toggle(entity: ApiHomeEntity) {
    await run(() => updateHomeEntity(entity.id, { enabled: !entity.enabled }), "Varlık güncellenemedi.");
  }

  async function setDirect(entity: ApiHomeEntity, direct: boolean) {
    if (direct) {
      // From the confirmation dialog: its own error slot reports a failure.
      await updateHomeEntity(entity.id, { direct: true });
      await load();
      onDirectChanged?.();
      return;
    }
    if (await run(() => updateHomeEntity(entity.id, { direct: false }), "Doğrudan işareti kaldırılamadı.")) {
      onDirectChanged?.();
    }
  }

  async function remove(entity: ApiHomeEntity) {
    // Errors surface in the confirmation dialog (`errorFallback`).
    await deleteHomeEntity(entity.id);
    await load();
  }

  const entities = data?.results ?? [];

  return (
    <section className="flex flex-col gap-3" aria-labelledby="ha-entities-heading" data-testid="ha-entities-editor">
      <h2 id="ha-entities-heading" className="text-lg font-semibold tracking-tight">
        Home Assistant izin listesi
      </h2>
      <p className="text-sm text-muted-foreground">
        Friday yalnızca burada listelenen varlıklarda, yalnızca işaretli hizmetleri çağırabilir; her
        çağrı yine Onaylar sayfasında senin kararını bekler. Tek istisna: &quot;Doğrudan (onaysız)&quot;
        işaretlediğin medya oynatıcılarda oynat, duraklat, durdur ve ses komutları kartsız çalışır.
      </p>
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {data === null && !error ? <p className="text-sm text-muted-foreground">Yükleniyor…</p> : null}
      {data !== null && entities.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="ha-entities-empty">
          İzin listesi boş: Home Assistant&apos;ta hiçbir şey çağrılamaz.
        </p>
      ) : null}
      <ul className="flex flex-col gap-2" aria-label="İzinli varlıklar">
        {entities.map((entity) => (
          <EntityRow
            key={`${entity.id}:${entity.updated_at}`}
            entity={entity}
            rooms={rooms}
            options={data?.services[entity.domain] ?? []}
            busy={busy}
            onSave={save}
            onToggle={toggle}
            onDelete={remove}
            onDirect={setDirect}
          />
        ))}
      </ul>
      <AddEntity workspaceId={workspaceId} rooms={rooms} busy={busy} onAdd={add} />
    </section>
  );
}
