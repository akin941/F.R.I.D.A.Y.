"use client";

import Link from "next/link";
import { safeLink } from "@/lib/api/proactive";
import type { HudNotices } from "@/lib/hud/events";

/**
 * The HUD `notices` section (Phase 12 D5/D20): unread count and the five
 * latest titles, each a deep link. Read-only: nothing here marks a notice
 * read or decides anything.
 */
export function HudNoticesPanel({ notices }: { notices: HudNotices | null }) {
  const unread = notices?.unread ?? 0;
  return (
    <section
      className="flex min-w-0 flex-col gap-2 rounded-xl border border-border/60 p-3 sm:p-4"
      aria-labelledby="hud-notices"
      data-testid="hud-notices"
    >
      <h2 id="hud-notices" className="text-xs font-semibold tracking-wide text-hud-caption-muted uppercase">
        Bildirimler
      </h2>
      {unread === 0 ? (
        <p className="text-sm text-hud-caption-muted">Okunmamış bildirim yok.</p>
      ) : (
        <>
          <p className="text-sm text-hud-awaiting-approval" data-testid="hud-notices-count">
            {unread} okunmamış
          </p>
          <ul className="flex flex-col gap-1 text-sm">
            {(notices?.latest ?? []).map((item, index) => {
              const href = safeLink(item.link);
              return (
                <li key={`${index}-${item.title}`} className="truncate text-hud-caption">
                  {href ? (
                    <Link href={href} className="underline-offset-2 hover:underline">
                      {item.title}
                    </Link>
                  ) : (
                    item.title
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
      <Link href="/notices" className="text-xs text-hud-caption-muted underline underline-offset-2">
        Tümü
      </Link>
    </section>
  );
}
