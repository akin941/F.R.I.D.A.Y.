"use client";

import Link from "next/link";
import { useCodingSessions, useLearningDigest } from "@/components/memory/learning-queries";
import { formatDateTime, formatRelativeTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";
import type { CodingSessionStatus } from "@/types/api";

/**
 * Phase 8A branch 6 HUD cards: the coding-sessions panel (the slot Phase 6
 * left for it) and the morning-card summary. Read-only like every HUD
 * panel (D8): the morning card is decided on /memory/learning, one item at
 * a time, never from the HUD.
 */

const PANEL = "flex min-w-0 flex-col gap-2 rounded-xl border border-border/60 p-3 sm:p-4";
const HEADING = "text-xs font-semibold tracking-wide text-hud-caption-muted uppercase";
const MAX_ROWS = 4;

/**
 * Session status -> HUD text class (full names, so Tailwind sees them). Only HUD state tokens already checked
 * against `--hud-surface` (lib/theme/tokens.test.ts) are used.
 */
export const SESSION_HUD_TOKENS: Record<CodingSessionStatus, string> = {
  running: "text-hud-tool-running",
  waiting_for_owner: "text-hud-awaiting-approval",
  stopped: "text-hud-caption-muted",
  stale: "text-hud-caption-muted",
};

export function HudSessionsPanel() {
  const query = useCodingSessions();
  const sessions = query.data ?? [];
  const shown = sessions.slice(0, MAX_ROWS);
  const extra = sessions.length - shown.length;
  return (
    <section className={PANEL} aria-labelledby="hud-sessions" data-testid="hud-sessions" data-slot="sessions">
      <h2 id="hud-sessions" className={HEADING}>
        {tr.hud.sessions.heading}
      </h2>
      {query.isError ? (
        <p className="text-sm text-hud-error">{tr.hud.sessions.failed}</p>
      ) : query.isPending ? null : shown.length === 0 ? (
        <p className="text-sm text-hud-caption-muted" data-testid="hud-sessions-empty">
          {tr.hud.sessions.empty}
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5 text-sm">
          {shown.map((session) => (
            <li key={session.id} className="flex flex-col" data-status={session.status}>
              <span className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate text-hud-caption">
                  {session.project || session.session_id}
                </span>
                <span className={cn("shrink-0 text-xs", SESSION_HUD_TOKENS[session.status])}>
                  {tr.learning.sessions.status[session.status]}
                </span>
              </span>
              <span className="text-xs text-hud-caption-muted" title={formatDateTime(session.last_event_at)}>
                {session.workspace.name} · {formatRelativeTime(session.last_event_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="flex justify-between text-xs text-hud-caption-muted">
        <span>{extra > 0 ? tr.hud.sessions.more(extra) : null}</span>
        <Link href="/sessions" className="underline underline-offset-2">
          {tr.hud.sessions.all}
        </Link>
      </p>
    </section>
  );
}

export function HudLearningPanel() {
  const query = useLearningDigest();
  const count = query.data?.count ?? 0;
  return (
    <section className={PANEL} aria-labelledby="hud-learning" data-testid="hud-learning">
      <h2 id="hud-learning" className={HEADING}>
        {tr.hud.learning.heading}
      </h2>
      {query.isError ? (
        <p className="text-sm text-hud-error">{tr.hud.learning.failed}</p>
      ) : query.isPending ? null : count === 0 ? (
        <p className="text-sm text-hud-caption-muted">{tr.hud.learning.empty}</p>
      ) : (
        <p className="text-sm text-hud-awaiting-approval" data-testid="hud-learning-count">
          {tr.hud.learning.pending(count)}
        </p>
      )}
      {/* D8: a deep link to the decision page; the HUD never decides. */}
      <Link href="/memory/learning" className="text-xs text-hud-caption-muted underline underline-offset-2">
        {tr.hud.learning.open}
      </Link>
    </section>
  );
}
