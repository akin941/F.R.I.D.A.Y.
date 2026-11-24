"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { MicOff, ShieldCheck, ShieldOff } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { errorMessage } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { fetchConversations } from "@/lib/query/chat";
import { fetchVoiceLive, releaseVoiceMute } from "@/lib/voice/api";
import type { ApiConversation, ApiVoiceLive } from "@/types/api";

const copy = tr.liveVoice;
const RECENT_LIVE_CONVERSATIONS = 5;

function reasonText(reason: string): string {
  return copy.reasons[reason] ?? reason;
}

/**
 * The /voice "Canlı ses (Mac)" panel (Phase 11 D12, D15): the always-on
 * gate's verdict per measured wake model with its reasons, each Mac node's
 * storm-mute banner with the release button (the only way to release it:
 * the owner's click on screen), and the last live conversations. Read-only
 * otherwise: the mic mode is chosen on the Mac, never here (ADR-013
 * decision 5).
 */
export function LiveVoicePanel({ workspaceId }: { workspaceId: string | null }) {
  const id = useId();
  const [data, setData] = useState<ApiVoiceLive | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [releasing, setReleasing] = useState<string | null>(null);
  const [releaseError, setReleaseError] = useState<string | null>(null);
  // Keyed by workspace: a response for a previous workspace is never shown.
  const [listed, setListed] = useState<{ workspaceId: string; rows: ApiConversation[] } | null>(
    null,
  );
  const conversations = listed && listed.workspaceId === workspaceId ? listed.rows : [];

  function applyLoad(promise: Promise<ApiVoiceLive>, signal?: AbortSignal) {
    return promise
      .then((body) => {
        setData(body);
        setLoadError(null);
      })
      .catch((err: unknown) => {
        if (!signal?.aborted) setLoadError(errorMessage(err, copy.loadError));
      });
  }

  useEffect(() => {
    const controller = new AbortController();
    void applyLoad(fetchVoiceLive(controller.signal), controller.signal);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    let live = true;
    fetchConversations(workspaceId)
      .then((rows) => {
        if (!live) return;
        setListed({
          workspaceId,
          rows: rows
            .filter((row) => row.opened_via === "node_voice")
            .slice(0, RECENT_LIVE_CONVERSATIONS),
        });
      })
      .catch(() => {
        if (live) setListed({ workspaceId, rows: [] });
      });
    return () => {
      live = false;
    };
  }, [workspaceId]);

  async function release(deviceId: string) {
    setReleasing(deviceId);
    setReleaseError(null);
    try {
      await releaseVoiceMute(deviceId);
      await applyLoad(fetchVoiceLive());
    } catch (err) {
      setReleaseError(errorMessage(err, copy.releaseError));
    } finally {
      setReleasing(null);
    }
  }

  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="flex w-full max-w-xl flex-col gap-3 rounded-xl border p-4 text-sm"
      data-testid="live-voice-panel"
    >
      <h2 id={`${id}-heading`} className="font-medium">
        {copy.heading}
      </h2>
      <p className="text-xs text-muted-foreground">{copy.intro}</p>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}
      {!data && !loadError ? (
        <p className="text-xs text-muted-foreground" role="status">
          {copy.loading}
        </p>
      ) : null}

      {data ? (
        <>
          {data.nodes.map((node) =>
            node.mute ? (
              <Alert key={node.device_id} variant="destructive" data-testid="live-voice-storm">
                <AlertDescription className="flex flex-col gap-2">
                  <span className="flex items-center gap-2">
                    <MicOff aria-hidden className="size-4" />
                    {copy.stormBanner(node.name)}
                  </span>
                  <span className="text-xs">
                    {copy.stormSince(formatDateTime(node.mute.engaged_at))}
                  </span>
                  <span>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={releasing === node.device_id}
                      data-testid="live-voice-release"
                      onClick={() => void release(node.device_id)}
                    >
                      {releasing === node.device_id ? copy.releasing : copy.release}
                    </Button>
                  </span>
                </AlertDescription>
              </Alert>
            ) : (
              <p key={node.device_id} className="text-xs text-muted-foreground">
                {copy.notMuted(node.name)}
              </p>
            ),
          )}
          {data.nodes.length === 0 ? (
            <p className="text-xs text-muted-foreground">{copy.noNodes}</p>
          ) : null}
          {releaseError ? (
            <p role="alert" className="text-xs text-destructive">
              {releaseError}
            </p>
          ) : null}

          <p
            className="flex items-center gap-2 font-medium"
            data-testid="live-voice-verdict"
            data-permitted={data.any_model_permitted}
          >
            {data.any_model_permitted ? (
              <ShieldCheck aria-hidden className="size-4" />
            ) : (
              <ShieldOff aria-hidden className="size-4" />
            )}
            {data.any_model_permitted ? copy.permitted : copy.notPermitted}
          </p>
          {!data.always_on_enabled ? (
            <p className="text-xs text-muted-foreground">{copy.settingOff}</p>
          ) : null}
          <p className="text-xs text-muted-foreground">
            {copy.budgets(data.max_far_per_hour, data.max_frr, data.min_household_hours)}
          </p>

          {data.models.length === 0 ? (
            <p className="text-xs text-muted-foreground">{copy.noModels}</p>
          ) : (
            <ul className="flex flex-col gap-2" data-testid="live-voice-models">
              {data.models.map((model) => (
                <li
                  key={model.model_sha256}
                  className="flex flex-col gap-0.5 text-xs"
                  data-permitted={model.permitted}
                >
                  <span className="font-medium" title={model.model_sha256}>
                    {copy.model(model.model_sha256)}
                    {model.threshold !== null && model.refractory_ms !== null
                      ? ` — ${copy.measuredAt(model.threshold, model.refractory_ms)}`
                      : ""}
                  </span>
                  {model.household?.far_per_hour ? (
                    <span>{copy.household(model.household.hours, model.household.far_per_hour)}</span>
                  ) : null}
                  {model.positives?.frr ? (
                    <span>{copy.positives(model.positives.positives, model.positives.frr)}</span>
                  ) : null}
                  <span data-testid="live-voice-reasons">
                    {model.permitted
                      ? copy.permitted
                      : model.reasons.map(reasonText).join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}

      <div className="flex flex-col gap-1">
        <h3 className="text-xs font-medium text-muted-foreground">{copy.conversationsHeading}</h3>
        {conversations.length === 0 ? (
          <p className="text-xs text-muted-foreground">{copy.noConversations}</p>
        ) : (
          <ul className="flex flex-col gap-0.5 text-xs" data-testid="live-voice-conversations">
            {conversations.map((conversation) => (
              <li key={conversation.id}>
                <Link href="/chat" className="underline underline-offset-2">
                  {conversation.title || copy.heading}
                </Link>{" "}
                <span className="text-muted-foreground">
                  {formatDateTime(conversation.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
