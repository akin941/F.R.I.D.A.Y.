"use client";

import { useQuery } from "@tanstack/react-query";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import { queryKeys } from "@/lib/query/keys";
import { POLL_FAST_MS, POLL_SLOW_MS, pollWhile } from "@/lib/query/polling";
import type { ApiCodingSession, ApiLearningDigest, ApiWorkspaceSummary } from "@/types/api";

/**
 * Phase 8A branch 6 shared queries. The morning card and the coding
 * sessions are cross-workspace aggregates (the backend scopes them with
 * `for_actor()` and labels every row with its workspace), so the page and
 * the HUD card dedup into one request each.
 */

/** Sessions refresh quickly while one is live; slowly otherwise. */
export function sessionsPollInterval(sessions: ApiCodingSession[]): number {
  return sessions.some((s) => s.status === "running" || s.status === "waiting_for_owner")
    ? POLL_FAST_MS * 5
    : POLL_SLOW_MS;
}

export function useCodingSessions() {
  return useQuery({
    queryKey: queryKeys.codingSessions(),
    queryFn: () => apiGetJson<ApiCodingSession[]>("/api/memory/sessions"),
    refetchInterval: pollWhile<ApiCodingSession[]>(sessionsPollInterval),
  });
}

export function useLearningDigest() {
  return useQuery({
    queryKey: queryKeys.learningDigest(),
    queryFn: () => apiGetJson<ApiLearningDigest>("/api/memory/learning/digest"),
    refetchInterval: pollWhile<ApiLearningDigest>(() => POLL_SLOW_MS * 10),
  });
}

/**
 * The owner's personal workspace: where the ingestion sources live (D2),
 * whatever workspace is active in the shell.
 */
export function usePersonalWorkspace(): ApiWorkspaceSummary | null {
  const { workspaces } = useWorkspace();
  return workspaces.find((w) => w.type === "personal" && w.role === "owner") ?? null;
}
