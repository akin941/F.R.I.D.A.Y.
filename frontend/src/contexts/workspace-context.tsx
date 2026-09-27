"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { apiGetJson } from "@/lib/api-client";
import type {
  ApiSecurityNotice,
  ApiUser,
  ApiWorkspaceSummary,
  SessionResponse,
} from "@/types/api";

interface WorkspaceContextValue {
  user: ApiUser | null;
  workspaces: ApiWorkspaceSummary[];
  securityNotices: ApiSecurityNotice[];
  activeWorkspace: ApiWorkspaceSummary | null;
  setActiveWorkspaceId: (workspaceId: string) => void;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

function storageKey(userId: string) {
  return `jarvis:active-workspace:${userId}`;
}

function pickDefaultWorkspace(
  workspaces: ApiWorkspaceSummary[],
  savedId: string | null,
): ApiWorkspaceSummary | null {
  if (workspaces.length === 0) {
    return null;
  }
  const saved = savedId ? workspaces.find((w) => w.id === savedId) : undefined;
  if (saved) {
    return saved;
  }
  const personal = workspaces.find((w) => w.type === "personal");
  return personal ?? workspaces[0] ?? null;
}

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<ApiUser | null>(null);
  const [workspaces, setWorkspaces] = useState<ApiWorkspaceSummary[]>([]);
  const [securityNotices, setSecurityNotices] = useState<ApiSecurityNotice[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const session = await apiGetJson<SessionResponse>("/api/auth/session");
      setUser(session.user);
      setWorkspaces(session.workspaces);
      setSecurityNotices(session.security_notices ?? []);
      const savedId =
        typeof window !== "undefined"
          ? window.localStorage.getItem(storageKey(session.user.id))
          : null;
      const initial = pickDefaultWorkspace(session.workspaces, savedId);
      setActiveWorkspaceIdState(initial?.id ?? null);
    } catch {
      setError("Failed to load session.");
    } finally {
      setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount for the session, the standard data-fetching effect pattern */
  useEffect(() => {
    void load();
  }, [load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const setActiveWorkspaceId = useCallback(
    (workspaceId: string) => {
      setActiveWorkspaceIdState(workspaceId);
      if (user && typeof window !== "undefined") {
        window.localStorage.setItem(storageKey(user.id), workspaceId);
      }
    },
    [user],
  );

  const activeWorkspace = useMemo(
    () => workspaces.find((w) => w.id === activeWorkspaceId) ?? null,
    [workspaces, activeWorkspaceId],
  );

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      user,
      workspaces,
      securityNotices,
      activeWorkspace,
      setActiveWorkspaceId,
      loading,
      error,
      refresh: load,
    }),
    [user, workspaces, securityNotices, activeWorkspace, setActiveWorkspaceId, loading, error, load],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    throw new Error("useWorkspace must be used within a WorkspaceProvider");
  }
  return ctx;
}
