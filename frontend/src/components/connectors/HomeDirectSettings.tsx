"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SERVICE_LABELS } from "@/components/connectors/HomeEntitiesEditor";
import { errorMessage } from "@/lib/api-client";
import { formatDateTime, formatRelativeTime } from "@/lib/format/relative-time";
import { fetchHomeDirectCalls, fetchHomeDirectSettings, updateHomeDirectSettings } from "@/lib/api/home";
import { TONE_PILL_CLASSES } from "@/lib/theme/status";
import type { ApiHomeDirectCall, ApiHomeDirectSettings, ApiHomeDirectState } from "@/types/api";

/** How many direct calls the panel lists (D11: "the last 20 calls"). */
export const DIRECT_CALLS_SHOWN = 20;

export const DIRECT_STATE_LABELS: Record<ApiHomeDirectState, string> = {
  on: "Açık",
  off_owner: "Kapalı (sen kapattın)",
  off_env: "Kapalı (sunucu ortam ayarı)",
};

const ORIGIN_LABELS: Record<ApiHomeDirectCall["origin"], string> = {
  voice: "web sesi",
  node: "Mac sesi",
  chat: "yazılı sohbet",
};

const STATUS_LABELS: Record<ApiHomeDirectCall["status"], string> = {
  running: "gönderiliyor",
  succeeded: "tamam",
  failed: "başarısız",
  unknown_outcome: "sonuç bilinmiyor",
};

const percent = (value: number) => Math.round(value * 100);

function volumeOf(data: Record<string, unknown> | null): number | null {
  const level = data?.volume_level;
  return typeof level === "number" ? level : null;
}

function CallRow({ call }: { call: ApiHomeDirectCall }) {
  const sent = volumeOf(call.data);
  const asked = volumeOf(call.requested_data);
  const tone =
    call.status === "succeeded" ? undefined : call.status === "failed" ? TONE_PILL_CLASSES.danger : TONE_PILL_CLASSES.warning;
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm" data-testid="ha-direct-call" data-status={call.status}>
      <span className="text-xs text-muted-foreground" title={formatDateTime(call.created_at)}>
        {formatRelativeTime(call.created_at)}
      </span>
      <code className="font-mono text-xs break-all">{call.entity_id}</code>
      <span>{SERVICE_LABELS[call.service] ?? call.service}</span>
      {sent !== null ? <span className="text-xs">%{percent(sent)}</span> : null}
      {asked !== null && asked !== sent ? (
        <span className="text-xs text-muted-foreground" data-testid="ha-direct-call-clamped">
          (istenen %{percent(asked)})
        </span>
      ) : null}
      <span className="text-xs text-muted-foreground">{ORIGIN_LABELS[call.origin] ?? call.origin}</span>
      <Badge variant="outline" className={tone}>
        {STATUS_LABELS[call.status] ?? call.status}
      </Badge>
    </li>
  );
}

/**
 * The direct media class's owner panel (Phase 13b D6/D11, ADR-018) on
 * `/connectors` under the Home Assistant allowlist: the state, the owner
 * kill switch ("Doğrudan medyayı kapat"), the volume maximum (0-100 %), the
 * environment kill notice (which disables the toggle: only the server's
 * environment can turn it back on), the closed list of services, the rate
 * limits and the last 20 direct calls. Owner, web session, personal
 * workspace only -- enforced by the backend.
 */
export function HomeDirectSettings({ workspaceId, refreshKey = 0 }: { workspaceId: string; refreshKey?: number }) {
  const [settings, setSettings] = useState<ApiHomeDirectSettings | null>(null);
  const [calls, setCalls] = useState<ApiHomeDirectCall[] | null>(null);
  const [volume, setVolume] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await fetchHomeDirectSettings(workspaceId);
      setSettings(next);
      setVolume(String(percent(next.volume_max)));
    } catch (err) {
      setError(errorMessage(err, "Doğrudan medya ayarları yüklenemedi."));
    }
    try {
      setCalls((await fetchHomeDirectCalls(workspaceId, DIRECT_CALLS_SHOWN)).results);
    } catch {
      setCalls([]);
    }
  }, [workspaceId]);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount and on a flag change */
  useEffect(() => {
    void load();
  }, [load, refreshKey]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function patch(input: { kill_switch?: boolean; volume_max?: number }, fallback: string) {
    setBusy(true);
    setError(null);
    try {
      const next = await updateHomeDirectSettings(workspaceId, input);
      setSettings(next);
      setVolume(String(percent(next.volume_max)));
    } catch (err) {
      setError(errorMessage(err, fallback));
    } finally {
      setBusy(false);
    }
  }

  const volumeNumber = Number(volume);
  const volumeValid = volume.trim() !== "" && Number.isInteger(volumeNumber) && volumeNumber >= 0 && volumeNumber <= 100;
  const envOff = settings !== null && (settings.env_kill_switch || settings.state === "off_env");

  return (
    <section className="flex flex-col gap-3" aria-labelledby="ha-direct-heading" data-testid="ha-direct-settings">
      <Card>
        <CardHeader>
          <CardTitle id="ha-direct-heading" className="text-base">
            Doğrudan medya (onaysız)
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            &quot;Doğrudan&quot; işaretlediğin medya oynatıcılarda yalnızca aşağıdaki komutlar onay kartı olmadan
            çalışır; diğer her Home Assistant çağrısı yine Onaylar sayfasını bekler.
          </p>
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          {settings === null && !error ? <p className="text-sm text-muted-foreground">Yükleniyor…</p> : null}
          {settings !== null ? (
            <>
              <p className="flex flex-wrap items-center gap-2 text-sm" data-testid="ha-direct-state" data-state={settings.state}>
                <span className="font-medium">Durum:</span>
                <Badge
                  variant="outline"
                  className={settings.state === "on" ? TONE_PILL_CLASSES.warning : undefined}
                >
                  {DIRECT_STATE_LABELS[settings.state] ?? settings.state}
                </Badge>
                <span className="text-muted-foreground" data-testid="ha-direct-flagged">
                  {settings.flagged_count} cihaz işaretli
                </span>
              </p>
              {envOff ? (
                <Alert role="status" data-testid="ha-direct-env-notice">
                  <AlertDescription>
                    {settings.env_kill_switch
                      ? "Sunucunun ortam ayarı (HA_DIRECT_MEDIA_KILL_SWITCH) doğrudan medyayı kapattı; buradan açılamaz. Her komut onay kartıyla gelir."
                      : "Sunucunun dakikalık sınırı 0: doğrudan medya ortam ayarıyla kapalı. Her komut onay kartıyla gelir."}
                  </AlertDescription>
                </Alert>
              ) : null}
              <label htmlFor="ha-direct-kill" className="flex items-center gap-2 text-sm font-medium">
                <input
                  id="ha-direct-kill"
                  type="checkbox"
                  role="switch"
                  aria-checked={settings.kill_switch}
                  checked={settings.kill_switch}
                  disabled={busy || envOff}
                  onChange={() =>
                    void patch({ kill_switch: !settings.kill_switch }, "Anahtar değiştirilemedi.")
                  }
                />
                Doğrudan medyayı kapat
              </label>
              <form
                className="flex flex-wrap items-end gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (volumeValid) void patch({ volume_max: volumeNumber / 100 }, "Ses üst sınırı kaydedilemedi.");
                }}
              >
                <div className="flex flex-col gap-1">
                  <Label htmlFor="ha-direct-volume">Doğrudan ses üst sınırı (%)</Label>
                  <Input
                    id="ha-direct-volume"
                    type="number"
                    inputMode="numeric"
                    min={0}
                    max={100}
                    step={1}
                    className="w-28"
                    value={volume}
                    aria-invalid={!volumeValid}
                    onChange={(event) => setVolume(event.target.value)}
                  />
                </div>
                <Button
                  type="submit"
                  size="sm"
                  disabled={busy || !volumeValid || volumeNumber === percent(settings.volume_max)}
                >
                  Kaydet
                </Button>
                <p className="w-full text-xs text-muted-foreground">
                  Daha yüksek bir ses düzeyi istenirse bu sınıra ayarlanır; &quot;sesi aç&quot; sınırın altında
                  değilse onay kartıyla gelir.
                </p>
              </form>
              <div className="flex flex-col gap-1 text-sm">
                <h3 className="font-medium">Kapsanan komutlar (değiştirilemez)</h3>
                <ul className="flex flex-wrap gap-x-3 gap-y-1" data-testid="ha-direct-services" aria-label="Kapsanan komutlar">
                  {settings.services.map((service) => (
                    <li key={service} className="flex items-center gap-1">
                      <span>{SERVICE_LABELS[service] ?? service}</span>
                      <code className="font-mono text-xs text-muted-foreground">{service}</code>
                    </li>
                  ))}
                </ul>
              </div>
              <p className="text-sm text-muted-foreground" data-testid="ha-direct-limits">
                Sınırlar: dakikada en fazla {settings.rate_global_per_minute} doğrudan komut, cihaz başına{" "}
                {settings.rate_entity_per_minute}; fazlası onay kartıyla gelir.
              </p>
            </>
          ) : null}
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-medium">Son doğrudan komutlar</h3>
            {calls !== null && calls.length === 0 ? (
              <p className="text-sm text-muted-foreground" data-testid="ha-direct-calls-empty">
                Henüz doğrudan komut yok.
              </p>
            ) : null}
            {calls !== null && calls.length > 0 ? (
              <ul className="flex flex-col gap-1" aria-label="Son doğrudan komutlar">
                {calls.slice(0, DIRECT_CALLS_SHOWN).map((call) => (
                  <CallRow key={call.id} call={call} />
                ))}
              </ul>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </section>
  );
}
