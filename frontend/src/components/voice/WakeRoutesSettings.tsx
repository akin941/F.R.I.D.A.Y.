"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { useWorkspace } from "@/contexts/workspace-context";
import { errorMessage } from "@/lib/api-client";
import {
  routeLabel,
  createWakeAlias,
  deleteWakeAlias,
  fetchWakeRoutes,
  type WakeRouteList,
} from "@/lib/api/wake-routes";
import { cn } from "@/lib/utils";

/**
 * `/settings/wake-routes` (Phase 12 D8/D20): the code-defined wake routes
 * ("Hey Friday, günaydın") and the owner's own aliases for them. Routes
 * only read; an alias only adds a phrase. Owner, web session only -- no
 * voice turn, tool or skill can add an alias.
 */
export function WakeRoutesSettings() {
  const { activeWorkspace } = useWorkspace();
  const workspaceId = activeWorkspace?.id ?? null;
  const [data, setData] = useState<WakeRouteList | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const current = useRef<string | null>(null);

  const load = useCallback(async (id: string) => {
    try {
      const list = await fetchWakeRoutes(id);
      if (current.current === id) setData(list);
    } catch (err) {
      if (current.current === id) setError(errorMessage(err, "Sesli komutlar yüklenemedi."));
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-workspace-change */
  useEffect(() => {
    current.current = workspaceId;
    setData(null);
    setError(null);
    if (workspaceId) void load(workspaceId);
  }, [workspaceId, load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function add(route: string) {
    if (!workspaceId) return;
    const phrase = (drafts[route] ?? "").trim();
    if (!phrase) return;
    setBusy(true);
    setError(null);
    try {
      await createWakeAlias(workspaceId, route, phrase);
      setDrafts((prev) => ({ ...prev, [route]: "" }));
      await load(workspaceId);
    } catch (err) {
      setError(errorMessage(err, "Takma ad eklenemedi."));
    } finally {
      setBusy(false);
    }
  }

  async function remove(aliasId: string) {
    if (!workspaceId) return;
    setBusy(true);
    setError(null);
    try {
      await deleteWakeAlias(aliasId);
      await load(workspaceId);
    } catch (err) {
      setError(errorMessage(err, "Takma ad silinemedi."));
    } finally {
      setBusy(false);
    }
  }

  if (!workspaceId) {
    return <div className="p-6 text-sm text-muted-foreground">Kullanılabilir çalışma alanı yok.</div>;
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-6")} data-testid="wake-routes-settings">
      <PageHeader
        title="Sesli komutlar"
        description={
          "\"Hey Friday\" dedikten sonra bu ifadelerden biri tek başına söylenirse Friday yönlendiriciyi " +
          "atlayıp doğrudan cevap verir. Komutlar yalnızca okur; hiçbir şeyi onaylamaz ya da çalıştırmaz."
        }
      />
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {data === null && !error ? <p className="text-sm text-muted-foreground">Yükleniyor…</p> : null}
      {data?.routes.map((route) => {
        const label = routeLabel(route.name);
        const full = route.aliases.length >= data.limits.aliases_per_route;
        const inputId = `alias-${route.name}`;
        return (
          <Card key={route.name} data-testid={`wake-route-${route.name}`}>
            <CardHeader>
              <CardTitle className="text-base">{label.title}</CardTitle>
              {label.description ? (
                <p className="text-sm text-muted-foreground">{label.description}</p>
              ) : null}
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <div className="flex flex-wrap gap-2" aria-label="Varsayılan ifadeler">
                {route.phrases.map((phrase) => (
                  <span key={phrase} className="rounded-md bg-muted px-2 py-1 text-xs">
                    {phrase}
                  </span>
                ))}
              </div>
              <ul className="flex flex-col gap-1" aria-label="Takma adlar">
                {route.aliases.map((alias) => (
                  <li key={alias.id} className="flex items-center justify-between gap-2 text-sm">
                    <span>{alias.phrase}</span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      onClick={() => void remove(alias.id)}
                      aria-label={`${alias.phrase} takma adını sil`}
                    >
                      Sil
                    </Button>
                  </li>
                ))}
              </ul>
              <form
                className="flex gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  void add(route.name);
                }}
              >
                <label htmlFor={inputId} className="sr-only">
                  {label.title} için yeni takma ad
                </label>
                <Input
                  id={inputId}
                  value={drafts[route.name] ?? ""}
                  maxLength={data.limits.max_chars + 20}
                  placeholder={full ? "En fazla 5 takma ad" : "Yeni takma ad"}
                  disabled={busy || full}
                  onChange={(event) => setDrafts((prev) => ({ ...prev, [route.name]: event.target.value }))}
                />
                <Button type="submit" disabled={busy || full || !(drafts[route.name] ?? "").trim()}>
                  Ekle
                </Button>
              </form>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
