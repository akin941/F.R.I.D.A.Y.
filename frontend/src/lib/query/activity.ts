"use client";

import { useQuery } from "@tanstack/react-query";
import { apiGetJson } from "@/lib/api-client";
import { queryKeys } from "@/lib/query/keys";
import { POLL_SLOW_MS } from "@/lib/query/polling";
import type { ApiActivitySummary } from "@/types/api";

/**
 * The shell's badge feed: one cheap aggregate over every workspace
 * membership, refreshed slowly (and on focus; never while hidden) instead
 * of polling every list. Views invalidate it after a decision.
 */
export function useActivitySummary(enabled = true) {
  return useQuery({
    queryKey: queryKeys.activitySummary(),
    queryFn: () => apiGetJson<ApiActivitySummary>("/api/activity/summary"),
    refetchInterval: POLL_SLOW_MS,
    enabled,
  });
}
