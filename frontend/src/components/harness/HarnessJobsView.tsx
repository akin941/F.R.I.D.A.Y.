"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderGit2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { HarnessJobGoalForm } from "@/components/harness/HarnessJobGoalForm";
import { ImplementationJobsPanel } from "@/components/harness/ImplementationJobsPanel";
import type { ImplementationPrefill } from "@/components/harness/ImplementationJobForm";
import {
  HARNESS_JOB_IN_FLIGHT_STATUSES,
  HARNESS_JOB_STATUS_LABELS,
  IMPL_STATUS_LABELS,
  harnessFailureReasonLabel,
  isImplTerminal,
} from "@/components/harness/labels";
import {
  CARD_LINK_CLASSES,
  HARNESS_REPOS_HREF,
  harnessJobHref,
  implementationJobHref,
} from "@/components/harness/links";
import { useDiscoveryJobs, useImplementationJobs } from "@/components/harness/queries";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import type { ApiHarnessJob, ApiHarnessJobDetail } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

type HarnessTab = "discovery" | "implementation";

const copy = tr.harness.console;

export function HarnessJobsView() {
  const { activeWorkspace } = useWorkspace();

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {tr.common.noWorkspace}
      </div>
    );
  }
  // Keyed by workspace: switching workspace resets the tab and open forms;
  // the query keys isolate the rows themselves.
  return <HarnessConsole key={activeWorkspace.id} workspaceId={activeWorkspace.id} />;
}

/** `?tab=implementation&discovery=<jobId>`, read once after mount (not during prerender). */
function useConsoleLinkParams() {
  const [params, setParams] = useState<{ tab: HarnessTab; discovery: string | null } | null>(null);
  /* eslint-disable react-hooks/set-state-in-effect -- reading the URL once after mount (not available during prerender) */
  useEffect(() => {
    const search = new URLSearchParams(window.location.search);
    setParams({
      tab: search.get("tab") === "implementation" ? "implementation" : "discovery",
      discovery: search.get("discovery"),
    });
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */
  return params;
}

/**
 * `/harness` (Phase 4 branch 5): the live strip (every in-flight discovery
 * and implementation job) over the recent lists, one tab per kind. Every
 * row links to its routed detail; the repo registry is one link away.
 */
function HarnessConsole({ workspaceId }: { workspaceId: string }) {
  const linkParams = useConsoleLinkParams();
  if (!linkParams) {
    return (
      <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
        <Skeleton className="h-10 w-1/3" />
      </div>
    );
  }
  return (
    <HarnessConsoleBody
      workspaceId={workspaceId}
      initialTab={linkParams.tab}
      discoveryId={linkParams.discovery}
    />
  );
}

function HarnessConsoleBody({
  workspaceId,
  initialTab,
  discoveryId,
}: {
  workspaceId: string;
  initialTab: HarnessTab;
  discoveryId: string | null;
}) {
  const [tab, setTab] = useState<HarnessTab>(discoveryId ? "implementation" : initialTab);
  const [prefillConsumed, setPrefillConsumed] = useState(false);

  // A finished discovery job's "Uygulama iste" link seeds the
  // implementation form with its repo + goal.
  const discovery = useQuery({
    queryKey: queryKeys.harnessJob(workspaceId, discoveryId ?? ""),
    queryFn: () => apiGetJson<ApiHarnessJobDetail>(`/api/harness/jobs/${discoveryId}`),
    enabled: discoveryId != null,
  });
  const prefill: ImplementationPrefill | null =
    !prefillConsumed && discovery.data?.status === "succeeded"
      ? { repo: discovery.data.repo, goal: discovery.data.goal, discoveryJobId: discovery.data.id }
      : null;
  const prefillLoading = discoveryId != null && discovery.isPending;

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <PageHeader
        title="Harness"
        description="Keşif: kayıtlı bir depoda salt okunur bir inceleme çalıştır, brifing al. Uygulama: ajandan değişiklik iste — plan ve gönderim ayrı ayrı senin onayından geçer."
        actions={
          <Button variant="outline" asChild>
            <Link href={HARNESS_REPOS_HREF}>
              <FolderGit2 aria-hidden />
              {copy.repos}
            </Link>
          </Button>
        }
      />

      <LiveJobs workspaceId={workspaceId} />

      <Tabs value={tab} onValueChange={(value) => setTab(value as HarnessTab)}>
        <TabsList aria-label="Harness işleri">
          <TabsTrigger value="discovery">Keşif işleri</TabsTrigger>
          <TabsTrigger value="implementation">Uygulama işleri</TabsTrigger>
        </TabsList>
        <TabsContent value="discovery" className="pt-2">
          <DiscoveryJobsPanel workspaceId={workspaceId} />
        </TabsContent>
        <TabsContent value="implementation" className="pt-2">
          {prefillLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : (
            <ImplementationJobsPanel
              workspaceId={workspaceId}
              prefill={prefill}
              onPrefillConsumed={() => setPrefillConsumed(true)}
            />
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

/** Every job still moving (or waiting at a gate), both kinds, newest first. */
function LiveJobs({ workspaceId }: { workspaceId: string }) {
  const discovery = useDiscoveryJobs(workspaceId);
  const implementation = useImplementationJobs(workspaceId);
  if (!discovery.data || !implementation.data) return null;

  const live = [
    ...discovery.data
      .filter((job) => HARNESS_JOB_IN_FLIGHT_STATUSES.includes(job.status))
      .map((job) => ({
        id: job.id,
        kind: copy.discoveryJob,
        goal: job.goal,
        createdAt: job.created_at,
        href: harnessJobHref(job),
        pill: (
          <StatusPill
            domain="harnessJob"
            status={job.status}
            label={HARNESS_JOB_STATUS_LABELS[job.status] ?? job.status}
          />
        ),
      })),
    ...implementation.data
      .filter((job) => !isImplTerminal(job.status))
      .map((job) => ({
        id: job.id,
        kind: copy.implementationJob,
        goal: job.goal,
        createdAt: job.created_at,
        href: implementationJobHref(job),
        pill: (
          <StatusPill
            domain="implementation"
            status={job.status}
            label={IMPL_STATUS_LABELS[job.status] ?? job.status}
          />
        ),
      })),
  ].sort((a, b) => b.createdAt.localeCompare(a.createdAt));

  return (
    <section aria-labelledby="harness-live-heading" className="flex flex-col gap-2">
      <h2 id="harness-live-heading" className="text-lg font-medium tracking-tight">
        {copy.live} {live.length > 0 ? `(${live.length})` : ""}
      </h2>
      {live.length === 0 ? (
        <p className="text-sm text-muted-foreground">{copy.noLive}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {live.map((item) => (
            <li key={item.id}>
              <Link
                href={item.href}
                className={cn(
                  CARD_LINK_CLASSES,
                  "flex flex-wrap items-center gap-2 border px-3 py-2 text-sm",
                )}
              >
                <span className="text-xs text-muted-foreground">{item.kind}</span>
                <span className="min-w-0 flex-1 truncate font-medium">{item.goal}</span>
                {item.pill}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * "Keşif işleri": the discovery job list + request form. Shares its query
 * (and poller) with the live strip.
 */
function DiscoveryJobsPanel({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const jobs = useDiscoveryJobs(workspaceId);

  function handleCreated(job: ApiHarnessJob) {
    queryClient.setQueryData<ApiHarnessJob[]>(queryKeys.harnessJobs(job.workspace), (prev = []) => [
      job,
      ...prev.filter((j) => j.id !== job.id),
    ]);
    setCreating(false);
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex justify-end">
        <Button onClick={() => setCreating((v) => !v)}>{creating ? "İptal" : "Yeni iş"}</Button>
      </div>

      {jobs.isError && !jobs.data ? (
        <ErrorState
          error={jobs.error}
          title="Harness işleri yüklenemedi."
          onRetry={() => void jobs.refetch()}
          retrying={jobs.isFetching}
        />
      ) : null}

      {creating ? (
        <HarnessJobGoalForm
          workspaceId={workspaceId}
          onCreated={handleCreated}
          onCancel={() => setCreating(false)}
        />
      ) : null}

      {jobs.isPending ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : jobs.data?.length === 0 ? (
        <p className="text-sm text-muted-foreground">Henüz harness işi yok.</p>
      ) : jobs.data ? (
        <ul className="flex flex-col gap-3">
          {jobs.data.map((job) => (
            <li key={job.id}>
              <Link href={harnessJobHref(job)} className={CARD_LINK_CLASSES}>
                <Card className="bg-transparent">
                  <CardHeader>
                    <CardTitle className="text-base">{job.goal}</CardTitle>
                  </CardHeader>
                  <CardContent className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                    <StatusPill
                      domain="harnessJob"
                      status={job.status}
                      label={HARNESS_JOB_STATUS_LABELS[job.status] ?? job.status}
                    />
                    {job.failure_reason ? (
                      <span>{harnessFailureReasonLabel(job.failure_reason)}</span>
                    ) : null}
                  </CardContent>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
