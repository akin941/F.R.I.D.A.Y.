"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Pause, Volume2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { useDigestPlayer, type DigestPlayerDeps } from "@/components/proactive/use-digest-player";
import { ackDigest, safeLink, type DigestSection } from "@/lib/api/proactive";
import { formatDateTime } from "@/lib/format/relative-time";
import { markSpokenOnOpen, shouldSpeakOnOpen } from "@/lib/proactive/digest-speech";
import { cn } from "@/lib/utils";

/**
 * The morning digest card (Phase 12 D6/D7/D20) on `/today` and the HUD:
 * the spoken text, "Dinle" (sentence by sentence through the speech
 * endpoint, nothing when muted, the mic never opened) and, on `/today`,
 * "Ayrıntı" (the facts by section with their links). Read-only: every link
 * opens the page where the owner acts; nothing here decides.
 */
export interface DigestCardData {
  id: string;
  created_at: string | null;
  spoken_text: string;
  speak_on_open: boolean;
  spoken: boolean;
  sections?: DigestSection[];
}

export interface DigestCardProps {
  digest: DigestCardData | null;
  workspaceId: string | null;
  variant?: "today" | "hud";
  /** HUD: speak once on open when the schedule asks for it. */
  autoSpeak?: boolean;
  /** `/today`: acknowledge `opened` once. */
  ackOpened?: boolean;
  playerDeps?: DigestPlayerDeps;
}

export function DigestCard({
  digest,
  workspaceId,
  variant = "today",
  autoSpeak = false,
  ackOpened = false,
  playerDeps,
}: DigestCardProps) {
  const { playing, play, stop } = useDigestPlayer(workspaceId, playerDeps);
  const [details, setDetails] = useState(false);
  const ackedRef = useRef<string | null>(null);
  const autoRef = useRef<string | null>(null);

  useEffect(() => {
    if (!digest || !ackOpened || ackedRef.current === digest.id) return;
    ackedRef.current = digest.id;
    void (playerDeps?.ack ?? ackDigest)(digest.id, "opened").catch(() => undefined);
  }, [digest, ackOpened, playerDeps]);

  useEffect(() => {
    if (!digest || !autoSpeak || autoRef.current === digest.id) return;
    autoRef.current = digest.id;
    if (!shouldSpeakOnOpen(digest)) return;
    markSpokenOnOpen(digest.id);
    play(digest);
  }, [digest, autoSpeak, play]);

  const hud = variant === "hud";
  if (!digest) {
    return (
      <section
        className={cn(hud ? "rounded-xl border border-border/60 p-3 text-sm" : "text-sm text-muted-foreground")}
        data-testid="digest-card-empty"
      >
        {hud ? <h2 className="text-xs font-semibold tracking-wide text-hud-caption-muted uppercase">Brifing</h2> : null}
        <p className={hud ? "text-hud-caption-muted" : undefined}>Bugün için brifing yok.</p>
      </section>
    );
  }

  const controls = playing ? (
    <Button size="sm" variant="outline" onClick={stop} data-testid="digest-stop">
      <Pause aria-hidden className="size-4" />
      Durdur
    </Button>
  ) : (
    <Button size="sm" onClick={() => play(digest)} data-testid="digest-listen">
      <Volume2 aria-hidden className="size-4" />
      Dinle
    </Button>
  );

  if (hud) {
    return (
      <section
        className="flex min-w-0 flex-col gap-2 rounded-xl border border-border/60 p-3 sm:p-4"
        aria-labelledby="hud-digest"
        data-testid="hud-digest"
      >
        <h2 id="hud-digest" className="text-xs font-semibold tracking-wide text-hud-caption-muted uppercase">
          Brifing
        </h2>
        <p className="text-sm text-hud-caption" data-testid="digest-text">
          {digest.spoken_text}
        </p>
        <div className="flex items-center justify-between gap-2">
          {controls}
          <Link href="/today" className="text-xs text-hud-caption-muted underline underline-offset-2">
            Bugün sayfası
          </Link>
        </div>
      </section>
    );
  }

  return (
    <Card data-testid="digest-card">
      <CardHeader>
        <CardTitle>Sabah brifingi</CardTitle>
        {digest.created_at ? (
          <p className="text-xs text-muted-foreground">{formatDateTime(digest.created_at)}</p>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm" data-testid="digest-text">
          {digest.spoken_text}
        </p>
        {details && digest.sections ? (
          <dl className="flex flex-col gap-2 text-sm" data-testid="digest-details">
            {digest.sections.map((section) => (
              <div key={section.section}>
                <dt className="font-medium">{section.title}</dt>
                {section.entries.map((entry) => {
                  const href = safeLink(entry.link);
                  return (
                    <dd key={entry.probe} className="ml-3 text-muted-foreground">
                      {href ? (
                        <Link href={href} className="underline underline-offset-2">
                          {entry.name}: {entry.count}
                        </Link>
                      ) : (
                        <span>
                          {entry.name}: {entry.count}
                        </span>
                      )}
                      {entry.items.length > 0 ? (
                        <ul className="ml-3 list-disc pl-3">
                          {entry.items.map((item, index) => (
                            <li key={`${entry.probe}-${index}`}>{item.label}</li>
                          ))}
                        </ul>
                      ) : null}
                    </dd>
                  );
                })}
              </div>
            ))}
          </dl>
        ) : null}
      </CardContent>
      <CardFooter className="flex justify-between gap-2">
        {controls}
        {digest.sections && digest.sections.length > 0 ? (
          <Button size="sm" variant="ghost" onClick={() => setDetails((v) => !v)} aria-expanded={details}>
            Ayrıntı
          </Button>
        ) : null}
      </CardFooter>
    </Card>
  );
}
