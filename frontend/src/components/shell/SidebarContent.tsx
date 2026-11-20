"use client";

import { signalAuthLost } from "@/lib/auth-events";
import { useId } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { NAV_GROUPS, activeNavHref, type NavBadge } from "@/components/shell/nav";
import { WorkspaceSwitcher } from "@/components/shell/WorkspaceSwitcher";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { VoiceMuteToggle } from "@/components/shell/VoiceMuteToggle";
import { HudMiniOrb } from "@/components/shell/HudMiniOrb";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiPostJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { useActivitySummary } from "@/lib/query/activity";
import type { ApiActivitySummary } from "@/types/api";

interface BadgeInfo {
  count: number;
  label: string;
  tone: "warning" | "info";
  // Pending approvals in OTHER workspaces: a dot, not a number.
  elsewhere?: number;
}

function badgeFor(
  badge: NavBadge | undefined,
  summary: ApiActivitySummary | undefined,
  workspaceId: string | undefined,
): BadgeInfo | null {
  if (!badge || !summary || !workspaceId) return null;
  const row = summary.workspaces.find((w) => w.workspace === workspaceId);
  if (badge === "approvals") {
    const count = row?.pending_approvals ?? 0;
    const elsewhere = summary.totals.pending_approvals - count;
    if (count === 0 && elsewhere <= 0) return null;
    return { count, label: tr.nav.pendingApprovals(count), tone: "warning", elsewhere };
  }
  const count = (badge === "harness" ? row?.live_harness_jobs : row?.live_agents) ?? 0;
  return count > 0 ? { count, label: tr.nav.liveWork(count), tone: "info" } : null;
}

function NavBadgeView({ info }: { info: BadgeInfo }) {
  return (
    <span className="ml-auto flex items-center gap-1.5">
      {info.count > 0 ? (
        <span
          aria-hidden
          className={cn(
            "min-w-5 rounded-full px-1.5 text-center text-xs leading-5 font-semibold tabular-nums",
            info.tone === "warning"
              ? "bg-warning text-warning-foreground"
              : "bg-info-muted text-info",
          )}
        >
          {info.count}
        </span>
      ) : null}
      {info.count > 0 ? <span className="sr-only">, {info.label}</span> : null}
      {info.elsewhere && info.elsewhere > 0 ? (
        <>
          <span
            aria-hidden
            title={tr.nav.pendingElsewhere(info.elsewhere)}
            className="size-2 rounded-full bg-warning"
          />
          <span className="sr-only">, {tr.nav.pendingElsewhere(info.elsewhere)}</span>
        </>
      ) : null}
    </span>
  );
}

export function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const activeHref = activeNavHref(pathname ?? "");
  const router = useRouter();
  const { user, loading, activeWorkspace } = useWorkspace();
  const activity = useActivitySummary(!loading && user != null);
  // The desktop aside and the mobile Sheet can both be mounted.
  const idPrefix = useId();

  async function handleLogout() {
    signalAuthLost();
    await apiPostJson("/api/auth/logout").catch(() => undefined);
    router.push("/enroll?signed_out=1");
    router.refresh();
  }

  return (
    <div className="flex h-full flex-col gap-4 p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-lg font-semibold tracking-tight">Friday</div>
        <HudMiniOrb onNavigate={onNavigate} />
      </div>
      <WorkspaceSwitcher />
      <Separator />
      <nav className="flex flex-col gap-4" aria-label={tr.nav.label}>
        {NAV_GROUPS.map((group) => (
          <div key={group.id} className="flex flex-col gap-0.5">
            <p
              id={`${idPrefix}-${group.id}`}
              className="px-3 pb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase"
            >
              {group.label}
            </p>
            <ul aria-labelledby={`${idPrefix}-${group.id}`} className="flex flex-col gap-0.5">
              {group.items.map((item) => {
                const active = activeHref === item.href;
                const badge = badgeFor(item.badge, activity.data, activeWorkspace?.id);
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors outline-none hover:bg-accent hover:text-accent-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
                        active && "bg-accent text-accent-foreground",
                      )}
                    >
                      <Icon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                      <span>{item.label}</span>
                      {badge ? <NavBadgeView info={badge} /> : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      <div className="mt-auto flex flex-col gap-2">
        <Separator />
        <div className="flex flex-wrap items-center gap-1">
          <ThemeToggle />
          <VoiceMuteToggle />
        </div>
        {loading ? (
          <Skeleton className="h-9 w-full" />
        ) : (
          <p className="truncate text-sm text-muted-foreground" title={user?.email}>
            {user?.name || user?.email}
          </p>
        )}
        <Button variant="outline" onClick={handleLogout}>
          Çıkış yap
        </Button>
      </div>
    </div>
  );
}
