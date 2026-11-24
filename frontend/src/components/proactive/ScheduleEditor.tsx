"use client";

import { useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  WEEKDAY_LABELS,
  fetchSchedules,
  maskFromWeekdays,
  saveSchedule,
  weekdaysFromMask,
  type Schedule,
  type ScheduleWrite,
} from "@/lib/api/proactive";
import { errorMessage } from "@/lib/api-client";

/**
 * The owner's schedule editor on `/today` (Phase 12 D3/D20): the morning
 * digest (time, days, "açılışta oku") and the heartbeat (interval, quiet
 * hours), each on/off. Only the owner's web session reaches the endpoint;
 * nothing a run, tool, skill or model does can create or edit a schedule.
 */

const INTERVALS = [15, 30, 60, 120] as const;

function hhmm(value: string | null | undefined): string {
  return value ? value.slice(0, 5) : "";
}

interface DigestForm {
  enabled: boolean;
  time: string;
  days: boolean[];
  speakOnOpen: boolean;
}

interface HeartbeatForm {
  enabled: boolean;
  interval: number;
  quietStart: string;
  quietEnd: string;
}

function digestForm(row: Schedule | undefined): DigestForm {
  return {
    enabled: row?.enabled ?? false,
    time: hhmm(row?.local_time) || "07:45",
    days: weekdaysFromMask(row?.weekdays ?? 0b0011111),
    speakOnOpen: row?.speak_on_open ?? false,
  };
}

function heartbeatForm(row: Schedule | undefined): HeartbeatForm {
  return {
    enabled: row?.enabled ?? false,
    interval: row?.interval_minutes ?? 30,
    quietStart: hhmm(row?.quiet_start),
    quietEnd: hhmm(row?.quiet_end),
  };
}

export function ScheduleEditor({ workspaceId }: { workspaceId: string }) {
  const [digest, setDigest] = useState<DigestForm>(digestForm(undefined));
  const [heartbeat, setHeartbeat] = useState<HeartbeatForm>(heartbeatForm(undefined));
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-workspace-change */
    setLoaded(false);
    setError(null);
    /* eslint-enable react-hooks/set-state-in-effect */
    fetchSchedules(workspaceId)
      .then((list) => {
        if (cancelled) return;
        setDigest(digestForm(list.schedules.find((s) => s.kind === "morning_digest")));
        setHeartbeat(heartbeatForm(list.schedules.find((s) => s.kind === "heartbeat")));
        setLoaded(true);
      })
      .catch(() => {
        if (!cancelled) setError("Zamanlamalar yüklenemedi.");
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  async function save(kind: "morning_digest" | "heartbeat") {
    setBusy(true);
    setError(null);
    setSaved(null);
    const body: ScheduleWrite =
      kind === "morning_digest"
        ? {
            enabled: digest.enabled,
            local_time: digest.time,
            weekdays: maskFromWeekdays(digest.days),
            speak_on_open: digest.speakOnOpen,
          }
        : {
            enabled: heartbeat.enabled,
            interval_minutes: heartbeat.interval,
            quiet_start: heartbeat.quietStart || null,
            quiet_end: heartbeat.quietEnd || null,
          };
    try {
      await saveSchedule(workspaceId, kind, body);
      setSaved(kind === "morning_digest" ? "Brifing zamanlaması kaydedildi." : "Nabız kaydedildi.");
    } catch (err) {
      setError(errorMessage(err, "Zamanlama kaydedilemedi."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card data-testid="schedule-editor">
      <CardHeader>
        <CardTitle>Zamanlamalar</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        {saved ? (
          <p role="status" className="text-xs text-muted-foreground">
            {saved}
          </p>
        ) : null}

        <fieldset className="flex flex-col gap-2" disabled={!loaded || busy}>
          <legend className="font-medium">Sabah brifingi</legend>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={digest.enabled}
              onChange={(e) => setDigest({ ...digest, enabled: e.target.checked })}
            />
            Açık
          </label>
          <div className="flex items-center gap-2">
            <Label htmlFor="digest-time">Saat</Label>
            <Input
              id="digest-time"
              type="time"
              className="w-32"
              value={digest.time}
              onChange={(e) => setDigest({ ...digest, time: e.target.value })}
            />
          </div>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Günler">
            {WEEKDAY_LABELS.map((label, index) => (
              <label key={label} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={digest.days[index]}
                  onChange={(e) => {
                    const days = [...digest.days];
                    days[index] = e.target.checked;
                    setDigest({ ...digest, days });
                  }}
                />
                {label}
              </label>
            ))}
          </div>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={digest.speakOnOpen}
              onChange={(e) => setDigest({ ...digest, speakOnOpen: e.target.checked })}
            />
            HUD açılınca bir kez sesli oku
          </label>
          <div>
            <Button size="sm" onClick={() => void save("morning_digest")} data-testid="save-digest">
              Kaydet
            </Button>
          </div>
        </fieldset>

        <fieldset className="flex flex-col gap-2" disabled={!loaded || busy}>
          <legend className="font-medium">Nabız (bildirimler)</legend>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={heartbeat.enabled}
              onChange={(e) => setHeartbeat({ ...heartbeat, enabled: e.target.checked })}
            />
            Açık
          </label>
          <div className="flex items-center gap-2">
            <Label htmlFor="heartbeat-interval">Aralık</Label>
            <select
              id="heartbeat-interval"
              className="rounded-md border border-input bg-transparent px-2 py-1"
              value={heartbeat.interval}
              onChange={(e) => setHeartbeat({ ...heartbeat, interval: Number(e.target.value) })}
            >
              {INTERVALS.map((minutes) => (
                <option key={minutes} value={minutes}>
                  {minutes} dk
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Label htmlFor="quiet-start">Sessiz saatler</Label>
            <Input
              id="quiet-start"
              type="time"
              className="w-32"
              value={heartbeat.quietStart}
              onChange={(e) => setHeartbeat({ ...heartbeat, quietStart: e.target.value })}
            />
            <span aria-hidden>–</span>
            <Input
              id="quiet-end"
              aria-label="Sessiz saat bitişi"
              type="time"
              className="w-32"
              value={heartbeat.quietEnd}
              onChange={(e) => setHeartbeat({ ...heartbeat, quietEnd: e.target.value })}
            />
          </div>
          <div>
            <Button size="sm" onClick={() => void save("heartbeat")} data-testid="save-heartbeat">
              Kaydet
            </Button>
          </div>
        </fieldset>
      </CardContent>
    </Card>
  );
}
