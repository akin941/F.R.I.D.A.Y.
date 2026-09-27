"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { apiGetJson, apiPostJson, apiDelete, errorMessage } from "@/lib/api-client";
import { useWorkspace } from "@/contexts/workspace-context";
import type { ApiTelegramLinkCode, ApiTelegramLinkStatus } from "@/types/api";

function secondsRemaining(expiresAt: string): number {
  return Math.max(0, Math.round((new Date(expiresAt).getTime() - Date.now()) / 1000));
}

/**
 * Carries its own workspace picker, same reasoning as CreateTokenForm — a
 * Telegram link is bound to one workspace for its lifetime (brief §3/§4),
 * not necessarily whichever workspace the sidebar switcher currently has
 * active.
 */
export function TelegramLinkPanel() {
  const { workspaces, activeWorkspace } = useWorkspace();

  const [workspaceId, setWorkspaceId] = useState(activeWorkspace?.id ?? "");
  const [status, setStatus] = useState<ApiTelegramLinkStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [linkCode, setLinkCode] = useState<ApiTelegramLinkCode | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [unlinking, setUnlinking] = useState(false);
  const [unlinkError, setUnlinkError] = useState<string | null>(null);
  const [remainingSeconds, setRemainingSeconds] = useState(0);

  const workspaceIdRef = useRef(workspaceId);

  const loadStatus = useCallback(async (id: string) => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await apiGetJson<ApiTelegramLinkStatus>(
        `/api/integrations/telegram/link-status?workspace=${encodeURIComponent(id)}`,
      );
      if (workspaceIdRef.current !== id) return;
      setStatus(data);
    } catch {
      if (workspaceIdRef.current === id) setLoadError("Telegram bağlantı durumu yüklenemedi.");
    } finally {
      if (workspaceIdRef.current === id) setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount/workspace-change, the standard data-fetching effect pattern */
  useEffect(() => {
    workspaceIdRef.current = workspaceId;
    setStatus(null);
    setLinkCode(null);
    if (workspaceId) {
      void loadStatus(workspaceId);
    }
  }, [workspaceId, loadStatus]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Live countdown to the code's expiry -- once it hits zero the code can no
  // longer be consumed (backend rejects it as expired), so the UI clears it
  // rather than let the user paste a code that will just fail.
  /* eslint-disable react-hooks/set-state-in-effect -- deriving the initial countdown value from the just-received code, the standard external-timer sync pattern */
  useEffect(() => {
    if (!linkCode) return;
    setRemainingSeconds(secondsRemaining(linkCode.expires_at));
    const interval = setInterval(() => {
      const remaining = secondsRemaining(linkCode.expires_at);
      setRemainingSeconds(remaining);
      if (remaining <= 0) {
        setLinkCode(null);
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [linkCode]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function handleRequestCode() {
    const requestedWorkspaceId = workspaceId;
    setRequesting(true);
    setRequestError(null);
    try {
      const code = await apiPostJson<ApiTelegramLinkCode>("/api/integrations/telegram/link-code", {
        workspace: requestedWorkspaceId,
      });
      // The response carries no workspace of its own -- if the picker moved
      // to a different workspace while this POST was in flight, showing this
      // code here would let the user paste a code for the wrong workspace
      // into their Telegram bot (Codex review, branch 4 round 2).
      if (workspaceIdRef.current !== requestedWorkspaceId) return;
      setLinkCode(code);
    } catch (err) {
      if (workspaceIdRef.current !== requestedWorkspaceId) return;
      setRequestError(
        errorMessage(err, "Kod oluşturulamadı."),
      );
    } finally {
      // Unconditional, unlike the two branches above -- `requesting` is a
      // plain in-flight flag for this panel, not workspace-scoped state, so
      // it must always clear once the request settles, even if the picker
      // moved on to a different workspace while it was in flight.
      setRequesting(false);
    }
  }

  async function handleUnlink() {
    const requestedWorkspaceId = workspaceId;
    setUnlinking(true);
    setUnlinkError(null);
    try {
      await apiDelete(
        `/api/integrations/telegram/link?workspace=${encodeURIComponent(requestedWorkspaceId)}`,
      );
      // Same staleness guard as handleRequestCode: if the picker moved on
      // while the DELETE was in flight, loadStatus(requestedWorkspaceId)
      // would set `loading` true for a workspace that's no longer selected
      // and then, quite reasonably, refuse to clear it again -- leaving the
      // panel stuck on its skeleton (Codex review, branch 4 round 3).
      if (workspaceIdRef.current !== requestedWorkspaceId) return;
      await loadStatus(requestedWorkspaceId);
    } catch (err) {
      if (workspaceIdRef.current !== requestedWorkspaceId) return;
      setUnlinkError(
        errorMessage(err, "Sohbet bağlantısı kaldırılamadı."),
      );
    } finally {
      setUnlinking(false);
    }
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium" htmlFor="telegram-workspace">
            Çalışma alanı
          </label>
          <Select value={workspaceId} onValueChange={setWorkspaceId}>
            <SelectTrigger id="telegram-workspace" aria-label="Telegram çalışma alanı">
              <SelectValue placeholder="Çalışma alanı seç" />
            </SelectTrigger>
            <SelectContent>
              {workspaces.map((workspace) => (
                <SelectItem key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {loadError ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{loadError}</AlertDescription>
          </Alert>
        ) : null}

        {loading || !status ? (
          <Skeleton className="h-16 w-full" />
        ) : status.linked ? (
          <div className="flex flex-col gap-2">
            <p className="text-sm">
              Şu sohbete bağlı: {status.chat_id}
              {status.linked_at
                ? ` (${new Date(status.linked_at).toLocaleString()} tarihinden beri)`
                : ""}
              .
            </p>
            {unlinkError ? (
              <Alert variant="destructive" role="alert">
                <AlertDescription>{unlinkError}</AlertDescription>
              </Alert>
            ) : null}
            <div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void handleUnlink()}
                disabled={unlinking}
              >
                {unlinking ? "Kaldırılıyor…" : "Bağlantıyı kaldır"}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-muted-foreground">Henüz bir Telegram sohbetine bağlı değil.</p>
            {requestError ? (
              <Alert variant="destructive" role="alert">
                <AlertDescription>{requestError}</AlertDescription>
              </Alert>
            ) : null}
            {linkCode ? (
              <Alert>
                <AlertTitle>Jarvis Telegram botuna yaz</AlertTitle>
                <AlertDescription className="flex flex-col gap-1">
                  <span>
                    Şunu gönder <code className="rounded-md bg-muted px-1 py-0.5">/link {linkCode.code}</code>
                  </span>
                  <span>
                    {remainingSeconds > 0
                      ? `${remainingSeconds}s içinde sona erecek`
                      : "Bu kodun süresi doldu — yeni bir tane iste."}
                  </span>
                </AlertDescription>
              </Alert>
            ) : (
              <div>
                <Button
                  onClick={() => void handleRequestCode()}
                  disabled={requesting || !workspaceId}
                >
                  {requesting ? "İsteniyor…" : "Telegram'ı bağla"}
                </Button>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
