"use client";

import { useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { useWorkspace } from "@/contexts/workspace-context";

/**
 * Follows `?workspace=<id>` on client-side navigations (a link into
 * another firm's item). The initial page load is handled by
 * WorkspaceProvider itself, so the first value seen here is only recorded,
 * never re-applied -- and a later manual switch in the sidebar is never
 * fought over while the URL still carries the old parameter.
 */
export function WorkspaceDeepLink() {
  const linkedId = useSearchParams().get("workspace");
  const { loading, followWorkspaceLink } = useWorkspace();
  const handledRef = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (loading) return;
    if (handledRef.current === undefined) {
      handledRef.current = linkedId;
      return;
    }
    if (linkedId === handledRef.current) return;
    handledRef.current = linkedId;
    if (linkedId) followWorkspaceLink(linkedId);
  }, [linkedId, loading, followWorkspaceLink]);

  return null;
}
