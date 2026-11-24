"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { useWorkspace } from "@/contexts/workspace-context";
import { errorMessage } from "@/lib/api-client";
import { dismissAllNotices, fetchNotices, markNoticeRead, safeLink, type Notice } from "@/lib/api/proactive";
import { formatRelativeTime } from "@/lib/format/relative-time";
import { cn } from "@/lib/utils";

/**
 * `/notices` (Phase 12 D5/D20): the workspace's proactive notices. A
 * notice decides nothing: its link opens the existing page where the owner
 * acts. Opening one marks it read; "Tümünü kapat" dismisses the unread ones.
 */
export function NoticesPanel() {
  const { activeWorkspace } = useWorkspace();
  const workspaceId = activeWorkspace?.id ?? null;
  const [notices, setNotices] = useState<Notice[]>([]);
  const [unread, setUnread] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const current = useRef<string | null>(null);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      const list = await fetchNotices(id, "all");
      if (current.current !== id) return;
      setNotices(list.notices);
      setUnread(list.unread);
    } catch {
      if (current.current === id) setError("Bildirimler yüklenemedi.");
    } finally {
      if (current.current === id) setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-workspace-change */
  useEffect(() => {
    current.current = workspaceId;
    setNotices([]);
    if (workspaceId) void load(workspaceId);
  }, [workspaceId, load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function open(notice: Notice) {
    if (notice.read_at || notice.dismissed_at) return;
    try {
      const updated = await markNoticeRead(notice.id);
      setNotices((prev) => prev.map((n) => (n.id === notice.id ? updated : n)));
      setUnread((count) => Math.max(0, count - 1));
    } catch {
      // The link still opens; the notice stays unread.
    }
  }

  async function dismissAll() {
    if (!workspaceId) return;
    try {
      await dismissAllNotices(workspaceId);
      await load(workspaceId);
    } catch (err) {
      setError(errorMessage(err, "Bildirimler kapatılamadı."));
    }
  }

  if (!workspaceId) {
    return <div className="p-6 text-sm text-muted-foreground">Kullanılabilir çalışma alanı yok.</div>;
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-6")} data-testid="notices-panel">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Bildirimler</h1>
          <p className="text-sm text-muted-foreground">
            {unread > 0 ? `${unread} okunmamış bildirim` : "Okunmamış bildirim yok."}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => void dismissAll()} disabled={unread === 0}>
          Tümünü kapat
        </Button>
      </div>
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {!loading && notices.length === 0 ? <p className="text-sm text-muted-foreground">Henüz bildirim yok.</p> : null}
      <ul className="flex flex-col gap-2">
        {notices.map((notice) => {
          const href = safeLink(notice.link);
          const fresh = !notice.read_at && !notice.dismissed_at;
          return (
            <li
              key={notice.id}
              data-testid="notice-row"
              data-unread={fresh ? "true" : "false"}
              className={cn("rounded-md border p-3 text-sm", fresh ? "border-warning" : "border-border opacity-80")}
            >
              {href ? (
                <Link href={href} onClick={() => void open(notice)} className="font-medium underline-offset-2 hover:underline">
                  {notice.title}
                </Link>
              ) : (
                <button type="button" className="text-left font-medium" onClick={() => void open(notice)}>
                  {notice.title}
                </button>
              )}
              <p className="text-xs text-muted-foreground">{formatRelativeTime(notice.created_at)}</p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
