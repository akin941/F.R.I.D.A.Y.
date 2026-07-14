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
import type { ApiPersonalAccessToken, ApiPersonalAccessTokenCreated } from "@/types/api";

export function IntegrationsView() {
  const { workspaces } = useWorkspace();

  const [tokens, setTokens] = useState<ApiPersonalAccessToken[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [revealedToken, setRevealedToken] = useState<string | null>(null);

  const loadTokens = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const list = await apiGetJson<ApiPersonalAccessToken[]>("/api/auth/tokens");
      setTokens(list);
    } catch {
      setLoadError("Failed to load personal access tokens.");
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
    setRevealedToken(rawToken);
    setCreating(false);
  }

  function handleChanged(token: ApiPersonalAccessToken) {
    setTokens((prev) => prev.map((t) => (t.id === token.id ? token : t)));
  }

  if (workspaces.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        No workspace available.
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Integrations</h1>
        <p className="text-sm text-muted-foreground">
          Reach Jarvis from a CLI or Telegram — new front doors onto the same chat pipeline, never
          a new way to approve or reject a request (that still requires the web app).
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h2 className="text-lg font-medium tracking-tight">Personal access tokens</h2>
          <Button onClick={() => setCreating((v) => !v)}>{creating ? "Cancel" : "New token"}</Button>
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
            key={revealedToken}
            token={revealedToken}
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
