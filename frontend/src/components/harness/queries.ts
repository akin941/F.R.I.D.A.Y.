"use client";

import { useQuery } from "@tanstack/react-query";
import { HARNESS_JOB_IN_FLIGHT_STATUSES, implPollInterval } from "@/components/harness/labels";
import { apiGetJson } from "@/lib/api-client";
import { queryKeys } from "@/lib/query/keys";
import { POLL_FAST_MS, fastestPollInterval, pollWhile } from "@/lib/query/polling";
import type { ApiHarnessJob, ApiHarnessRepo, ApiImplementationJob } from "@/types/api";

/**
 * The console's shared list queries. The live strip and each tab's list
 * call the same hook, so TanStack Query dedups them into one request and
 * one poller per list. Each polls only while some row is still in flight
 * (never while the tab is hidden) and stops once every row is terminal.
 */
export function useDiscoveryJobs(workspaceId: string) {
  return useQuery({
    queryKey: queryKeys.harnessJobs(workspaceId),
    queryFn: () =>
      apiGetJson<ApiHarnessJob[]>(`/api/harness/jobs?workspace=${encodeURIComponent(workspaceId)}`),
    refetchInterval: pollWhile<ApiHarnessJob[]>((list) =>
      list.some((j) => HARNESS_JOB_IN_FLIGHT_STATUSES.includes(j.status)) ? POLL_FAST_MS : null,
    ),
  });
}

export function useImplementationJobs(workspaceId: string) {
  return useQuery({
    queryKey: queryKeys.implementationJobs(workspaceId),
    queryFn: () =>
      apiGetJson<ApiImplementationJob[]>(
        `/api/harness/implementation-jobs?workspace=${encodeURIComponent(workspaceId)}`,
      ),
    refetchInterval: pollWhile<ApiImplementationJob[]>((list) =>
      fastestPollInterval(list.map((j) => implPollInterval(j.status))),
    ),
  });
}

/** The repo registry (curator-only; a member gets a 403 error state). */
export function useHarnessRepos(workspaceId: string) {
  return useQuery({
    queryKey: queryKeys.harnessRepos(workspaceId),
    queryFn: () =>
      apiGetJson<ApiHarnessRepo[]>(
        `/api/harness/repos?workspace=${encodeURIComponent(workspaceId)}`,
      ),
  });
}
