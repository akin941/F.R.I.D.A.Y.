"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { apiGetJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
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
  /**
   * Honor a `?workspace=<id>` deep link: switch to that workspace (with a
   * toast) when it is one of the user's and not already active; a toast
   * explains when it is not accessible. No-op while the session loads.
   */
  followWorkspaceLink: (workspaceId: string) => void;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

function storageKey(userId: string) {
  return `jarvis:active-workspace:${userId}`;
}

/** The `?workspace=` deep-link parameter of the page being loaded, if any. */
function linkedWorkspaceIdFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("workspace");
}

function notifyLinkResult(linked: ApiWorkspaceSummary | null) {
  if (linked) {
    toast.info(tr.workspace.switchedByLink(linked.name));
  } else {
    toast.error(tr.workspace.unknownFromLink);
  }
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
      let initial = pickDefaultWorkspace(session.workspaces, savedId);
      // A deep link into another workspace (e.g. a firm's approval) wins
      // over the saved choice -- picked here, before any view renders, so
      // the default workspace's data never flashes first.
      const linkedId = linkedWorkspaceIdFromLocation();
      if (linkedId && linkedId !== initial?.id) {
        const linked = session.workspaces.find((w) => w.id === linkedId) ?? null;
        if (linked) {
          initial = linked;
          window.localStorage.setItem(storageKey(session.user.id), linked.id);
        }
        notifyLinkResult(linked);
      }
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

  const followWorkspaceLink = useCallback(
    (workspaceId: string) => {
      if (loading || workspaceId === activeWorkspaceId) return;
      const linked = workspaces.find((w) => w.id === workspaceId) ?? null;
      if (linked) setActiveWorkspaceId(linked.id);
      notifyLinkResult(linked);
    },
    [loading, activeWorkspaceId, workspaces, setActiveWorkspaceId],
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
      followWorkspaceLink,
      loading,
      error,
      refresh: load,
    }),
    [
      user,
      workspaces,
      securityNotices,
      activeWorkspace,
      setActiveWorkspaceId,
      followWorkspaceLink,
      loading,
      error,
      load,
    ],
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
