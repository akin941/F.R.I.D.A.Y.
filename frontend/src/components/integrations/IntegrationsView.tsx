"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { CreateTokenForm } from "@/components/integrations/CreateTokenForm";
import { RevealedTokenBanner } from "@/components/integrations/RevealedTokenBanner";
import { TokenList } from "@/components/integrations/TokenList";
import { TelegramLinkPanel } from "@/components/integrations/TelegramLinkPanel";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import type { ApiPersonalAccessToken, ApiPersonalAccessTokenCreated, PatKind } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

export function IntegrationsView() {
  const { workspaces } = useWorkspace();

  const [tokens, setTokens] = useState<ApiPersonalAccessToken[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [revealedToken, setRevealedToken] = useState<{ token: string; kind: PatKind } | null>(
    null,
  );

  const loadTokens = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const list = await apiGetJson<ApiPersonalAccessToken[]>("/api/auth/tokens");
      setTokens(list);
    } catch {
      setLoadError("Kişisel erişim tokenları yüklenemedi.");
    } finally {
      setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount, the standard data-fetching effect pattern */
  useEffect(() => {
    void loadTokens();
  }, [loadTokens]);
  /* eslint-enable react-hooks/set-state-in-effect */

  function handleCreated(created: ApiPersonalAccessTokenCreated) {
    // The raw `token` field must never linger in the list's long-lived
    // state -- it's already fully handed off to `revealedToken` below, the
    // one place it's rendered, and dismissing that banner must actually end
    // its reachability, not just its visibility (Codex review, branch 4).
    const { token: rawToken, ...rest } = created;
    setTokens((prev) => [rest, ...prev]);
    setRevealedToken({ token: rawToken, kind: rest.kind });
    setCreating(false);
  }

  function handleChanged(token: ApiPersonalAccessToken) {
    setTokens((prev) => prev.map((t) => (t.id === token.id ? token : t)));
  }

  if (workspaces.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        Kullanılabilir çalışma alanı yok.
      </div>
    );
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Entegrasyonlar</h1>
        <p className="text-sm text-muted-foreground">
          Friday&apos;ye CLI, Telegram veya Open WebUI gibi OpenAI uyumlu bir istemci üzerinden ulaş —
          aynı sohbet hattına açılan yeni kapılar, bir isteği onaylamanın ya da reddetmenin yeni bir
          yolu değil (o hâlâ web uygulamasını gerektirir).
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h2 className="text-lg font-medium tracking-tight">Kişisel erişim tokenları</h2>
          <Button onClick={() => setCreating((v) => !v)}>{creating ? "İptal" : "Yeni token"}</Button>
        </div>

        {loadError ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{loadError}</AlertDescription>
          </Alert>
        ) : null}

        {revealedToken ? (
          // Keyed by the token itself: creating a second token without
          // dismissing the first's banner must not let React reuse the same
          // instance and carry over its "Copied" state -- that would show
          // "Copied" for a token that was never actually copied (Codex
          // review, branch 4 round 4).
          <RevealedTokenBanner
            key={revealedToken.token}
            token={revealedToken.token}
            kind={revealedToken.kind}
            onDismiss={() => setRevealedToken(null)}
          />
        ) : null}

        {creating ? (
          <CreateTokenForm onCreated={handleCreated} onCancel={() => setCreating(false)} />
        ) : null}

        {loading ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : (
          <TokenList tokens={tokens} onChanged={handleChanged} />
        )}
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium tracking-tight">Telegram</h2>
        <TelegramLinkPanel />
      </section>
    </div>
  );
}
