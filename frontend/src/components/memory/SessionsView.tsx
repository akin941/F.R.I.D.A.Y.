"use client";

import Link from "next/link";
import { TerminalSquare } from "lucide-react";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { harnessJobHref, implementationJobHref } from "@/components/harness/links";
import { useDiscoveryJobs, useImplementationJobs } from "@/components/harness/queries";
import { useCodingSessions } from "@/components/memory/learning-queries";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { useWorkspace } from "@/contexts/workspace-context";
import { formatDateTime, formatRelativeTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";
import type { ApiCodingSession } from "@/types/api";

const copy = tr.learning.sessions;

/**
 * One coding session. The project and last notification are scrubbed,
 * bounded tool/model text: rendered as plain text, never as markup.
 */
export function SessionRow({ session }: { session: ApiCodingSession }) {
  return (
    <li
      className="flex flex-col gap-1 rounded-md border px-3 py-2 text-sm"
      data-testid={`session-${session.id}`}
      data-status={session.status}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 font-medium break-all">{session.project || session.session_id}</span>
        <StatusPill domain="codingSession" status={session.status} label={copy.status[session.status]} />
        <span className="text-xs text-muted-foreground">{session.workspace.name}</span>
      </div>
      <p className="text-xs text-muted-foreground" title={formatDateTime(session.last_event_at)}>
        {copy.lastEvent(session.last_event || "—", formatRelativeTime(session.last_event_at))} ·{" "}
        {copy.events(session.event_count)}
      </p>
      {session.last_notification ? (
        <p className="text-xs break-words">
          <span className="text-muted-foreground">{copy.notification}: </span>
          {session.last_notification}
        </p>
      ) : null}
    </li>
  );
}

function CodingSessions() {
  const query = useCodingSessions();
  if (query.isPending) return <Skeleton className="h-24 w-full" />;
  if (query.isError) {
    return <ErrorState error={query.error} title={copy.loadFailed} onRetry={() => void query.refetch()} />;
  }
  if (query.data.length === 0) return <EmptyState icon={TerminalSquare} title={copy.empty} />;
  return (
    <ul className="flex flex-col gap-2" data-testid="coding-sessions">
      {query.data.map((session) => (
        <SessionRow key={session.id} session={session} />
      ))}
    </ul>
  );
}

/** The active workspace's harness jobs, composed client-side (memory may not import harness). */
function HarnessJobs({ workspaceId }: { workspaceId: string }) {
  const discovery = useDiscoveryJobs(workspaceId);
  const implementation = useImplementationJobs(workspaceId);
  if (discovery.isPending || implementation.isPending) return <Skeleton className="h-16 w-full" />;
  if (discovery.isError || implementation.isError) {
    return (
      <ErrorState
        error={discovery.error ?? implementation.error}
        title={copy.harnessFailed}
        onRetry={() => {
          void discovery.refetch();
          void implementation.refetch();
        }}
      />
    );
  }
  const rows = [
    ...discovery.data.map((job) => ({
      id: job.id,
      href: harnessJobHref(job),
      goal: job.goal,
      created: job.created_at,
      pill: <StatusPill domain="harnessJob" status={job.status} label={tr.status.harnessJob[job.status]} />,
    })),
    ...implementation.data.map((job) => ({
      id: job.id,
      href: implementationJobHref(job),
      goal: job.goal,
      created: job.created_at,
      pill: (
        <StatusPill domain="implementation" status={job.status} label={tr.status.implementation[job.status]} />
      ),
    })),
  ]
    .sort((a, b) => b.created.localeCompare(a.created))
    .slice(0, 10);
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">{copy.harnessEmpty}</p>;
  return (
    <ul className="flex flex-col gap-1.5" data-testid="sessions-harness-jobs">
      {rows.map((row) => (
        <li key={row.id} className="flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm">
          <Link href={row.href} className="min-w-0 flex-1 truncate underline-offset-2 hover:underline">
            {row.goal}
          </Link>
          {row.pill}
          <span className="text-xs text-muted-foreground">{formatRelativeTime(row.created)}</span>
        </li>
      ))}
    </ul>
  );
}

/** `/sessions` (Phase 8A D12): live Claude Code sessions next to the harness job list. */
export function SessionsView() {
  const { activeWorkspace } = useWorkspace();
  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-4 md:p-6")}>
      <PageHeader title={copy.title} description={copy.description} />
      <CodingSessions />
      <section className="flex flex-col gap-2" aria-labelledby="sessions-harness">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="sessions-harness" className="text-lg font-semibold tracking-tight">
            {copy.harness}
          </h2>
          <Link href="/harness" className="text-sm underline underline-offset-2">
            {copy.harnessAll}
          </Link>
        </div>
        {activeWorkspace ? (
          <HarnessJobs key={activeWorkspace.id} workspaceId={activeWorkspace.id} />
        ) : (
          <p className="text-sm text-muted-foreground">{tr.common.noWorkspace}</p>
        )}
      </section>
    </div>
  );
}
