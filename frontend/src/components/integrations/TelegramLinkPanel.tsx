"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ErrorState } from "@/components/ui/error-state";
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
import { formatDateTime } from "@/lib/format/relative-time";
import { queryKeys } from "@/lib/query/keys";
import { useCountdown } from "@/lib/use-countdown";
import type { ApiTelegramLinkCode, ApiTelegramLinkStatus } from "@/types/api";

/**
 * Carries its own workspace picker, same reasoning as CreateTokenForm — a
 * Telegram link is bound to one workspace for its lifetime (brief §3/§4),
 * not necessarily whichever workspace the sidebar switcher currently has
 * active.
 */
export function TelegramLinkPanel() {
  const { workspaces, activeWorkspace } = useWorkspace();
  const [workspaceId, setWorkspaceId] = useState(activeWorkspace?.id ?? "");

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
        {/* Keyed by the picked workspace: a code, an error or an in-flight
            request for one workspace never survives a switch to another
            (a POST that resolves after the switch lands on an unmounted
            component) -- so a code can never be shown for the wrong one. */}
        {workspaceId ? <TelegramLinkStatus key={workspaceId} workspaceId={workspaceId} /> : null}
      </CardContent>
    </Card>
  );
}

function TelegramLinkStatus({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const [linkCode, setLinkCode] = useState<ApiTelegramLinkCode | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [unlinking, setUnlinking] = useState(false);
  const [unlinkError, setUnlinkError] = useState<string | null>(null);

  const statusKey = queryKeys.telegramLinkStatus(workspaceId);
  const statusQuery = useQuery({
    queryKey: statusKey,
    queryFn: () =>
      apiGetJson<ApiTelegramLinkStatus>(
        `/api/integrations/telegram/link-status?workspace=${encodeURIComponent(workspaceId)}`,
      ),
  });
  const status = statusQuery.data ?? null;

  // Live countdown to the code's expiry -- once it hits zero the code can
  // no longer be consumed (the backend rejects it), so it is hidden rather
  // than letting the user paste a code that will just fail.
  const remainingSeconds = useCountdown(linkCode?.expires_at ?? null);
  const activeCode = linkCode && remainingSeconds > 0 ? linkCode : null;

  async function handleRequestCode() {
    setRequesting(true);
    setRequestError(null);
    try {
      const code = await apiPostJson<ApiTelegramLinkCode>("/api/integrations/telegram/link-code", {
        workspace: workspaceId,
      });
      setLinkCode(code);
    } catch (err) {
      setRequestError(errorMessage(err, "Kod oluşturulamadı."));
    } finally {
      setRequesting(false);
    }
  }

  async function handleUnlink() {
    setUnlinking(true);
    setUnlinkError(null);
    try {
      await apiDelete(
        `/api/integrations/telegram/link?workspace=${encodeURIComponent(workspaceId)}`,
      );
      await queryClient.invalidateQueries({ queryKey: statusKey });
    } catch (err) {
      setUnlinkError(errorMessage(err, "Sohbet bağlantısı kaldırılamadı."));
    } finally {
      setUnlinking(false);
    }
  }

  return (
    <>
      {statusQuery.isError && !status ? (
        <ErrorState
          error={statusQuery.error}
          title="Telegram bağlantı durumu yüklenemedi."
          onRetry={() => void statusQuery.refetch()}
          retrying={statusQuery.isFetching}
        />
      ) : !status ? (
        <Skeleton className="h-16 w-full" />
      ) : status.linked ? (
        <div className="flex flex-col gap-2">
          <p className="text-sm">
            Şu sohbete bağlı: {status.chat_id}
            {status.linked_at
              ? ` (${formatDateTime(status.linked_at)} tarihinden beri)`
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
          {activeCode ? (
            <Alert>
              <AlertTitle>Jarvis Telegram botuna yaz</AlertTitle>
              <AlertDescription className="flex flex-col gap-1">
                <span>
                  Şunu gönder <code className="rounded-md bg-muted px-1 py-0.5">/link {activeCode.code}</code>
                </span>
                <span>{`${remainingSeconds}s içinde sona erecek`}</span>
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
    </>
  );
}
