"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Bell } from "lucide-react";
import { useWorkspace } from "@/contexts/workspace-context";
import { fetchNotices } from "@/lib/api/proactive";
import { cn } from "@/lib/utils";

export const NOTICE_POLL_MS = 60_000;

/**
 * The shell bell (Phase 12 D5/D20): the active workspace's unread notice
 * count, polled; a link to `/notices`. Reading the count marks nothing.
 */
export function NoticeBell({ onNavigate, pollMs = NOTICE_POLL_MS }: { onNavigate?: () => void; pollMs?: number }) {
  const { activeWorkspace } = useWorkspace();
  const workspaceId = activeWorkspace?.id ?? null;
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    const load = () =>
      fetchNotices(workspaceId)
        .then((list) => {
          if (!cancelled) setUnread(list.unread);
        })
        .catch(() => undefined);
    void load();
    const timer = window.setInterval(() => void load(), pollMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [workspaceId, pollMs]);

  const label = unread > 0 ? `Bildirimler, ${unread} okunmamış` : "Bildirimler";
  return (
    <Link
      href="/notices"
      onClick={onNavigate}
      aria-label={label}
      title={label}
      data-testid="notice-bell"
      className="relative inline-flex size-8 items-center justify-center rounded-md hover:bg-accent"
    >
      <Bell aria-hidden className="size-4" />
      {unread > 0 ? (
        <span
          aria-hidden
          data-testid="notice-bell-count"
          className={cn(
            "absolute -top-1 -right-1 min-w-4 rounded-full bg-warning px-1 text-center text-[10px] leading-4 font-semibold text-warning-foreground tabular-nums",
          )}
        >
          {unread > 99 ? "99+" : unread}
        </span>
      ) : null}
    </Link>
  );
}
