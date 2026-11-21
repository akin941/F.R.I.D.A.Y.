"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Network } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { EntityTimeline } from "@/components/memory/EntityTimeline";
import { NATIVE_SELECT } from "@/components/memory/IngestionView";
import { LearningSubnav } from "@/components/memory/LearningSubnav";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { TONE_PILL_CLASSES } from "@/lib/theme/status";
import { cn } from "@/lib/utils";
import type { ApiGraphEntity, GraphEntityKind } from "@/types/api";

const copy = tr.learning.graph;

const KINDS = Object.keys(copy.kind) as GraphEntityKind[];

export function entitiesPath(workspaceId: string, q: string, kind: string): string {
  const params = new URLSearchParams({ workspace: workspaceId });
  if (q) params.set("q", q);
  if (kind) params.set("kind", kind);
  return `/api/memory/graph/entities?${params.toString()}`;
}

function EntityList({
  workspaceId,
  q,
  kind,
  selected,
  onSelect,
}: {
  workspaceId: string;
  q: string;
  kind: string;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const entities = useQuery({
    queryKey: queryKeys.graphEntities(workspaceId, q, kind),
    queryFn: () => apiGetJson<ApiGraphEntity[]>(entitiesPath(workspaceId, q, kind)),
  });
  if (entities.isPending) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }
  if (entities.isError) {
    return <ErrorState error={entities.error} title={copy.loadFailed} onRetry={() => void entities.refetch()} />;
  }
  if (entities.data.length === 0) return <EmptyState icon={Network} title={copy.empty} />;
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {copy.results(entities.data.length)}
      </p>
      <ul aria-label={copy.listLabel} className="flex flex-col gap-1" data-testid="graph-entities">
        {entities.data.map((entity) => (
          <li key={entity.id}>
            <button
              type="button"
              aria-current={selected === entity.id ? "true" : undefined}
              onClick={() => onSelect(entity.id)}
              className={cn(
                "flex w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-md border px-2 py-1.5 text-left text-sm outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50",
                selected === entity.id && "bg-accent text-accent-foreground",
              )}
            >
              <span className="min-w-0 font-medium break-words">{entity.name}</span>
              <span className="text-xs text-muted-foreground">{copy.kind[entity.kind] ?? entity.kind}</span>
              {entity.origin === "graphify" ? (
                <Badge variant="outline" className={TONE_PILL_CLASSES.neutral}>
                  {copy.graphify}
                </Badge>
              ) : null}
              {entity.is_private ? (
                <Badge variant="outline" className={TONE_PILL_CLASSES.info}>
                  {copy.private}
                </Badge>
              ) : null}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * `/memory/graph` (Phase 8B branch 9): an accessible list view of the
 * temporal graph -- search, then an entity page with its facts over time.
 * No canvas graph library: keyboard and screen-reader first; the visual
 * graph is Obsidian's job (D12). Owner workspaces only (the API is
 * owner-only and scopes with `for_actor()`).
 */
export function GraphView() {
  const { activeWorkspace, workspaces } = useWorkspace();
  const owned = workspaces.filter((w) => w.role === "owner");
  const initial =
    owned.find((w) => w.id === activeWorkspace?.id)?.id ??
    owned.find((w) => w.type === "personal")?.id ??
    owned[0]?.id ??
    "";
  const [workspaceId, setWorkspaceId] = useState(initial);
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setQ(draft.trim());
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-4 md:p-6")}>
      <PageHeader title={copy.title} description={copy.description} />
      <LearningSubnav />
      {!workspaceId ? (
        <p className="text-sm text-muted-foreground">{copy.noOwned}</p>
      ) : (
        <>
          <form role="search" className="grid gap-2 sm:grid-cols-[auto_1fr_auto_auto] sm:items-end" onSubmit={submit}>
            <div className="flex min-w-0 flex-col gap-1">
              <Label htmlFor="graph-workspace">{copy.workspace}</Label>
              <select
                id="graph-workspace"
                className={NATIVE_SELECT}
                value={workspaceId}
                onChange={(e) => {
                  setWorkspaceId(e.target.value);
                  setSelected(null);
                }}
              >
                {owned.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex min-w-0 flex-col gap-1">
              <Label htmlFor="graph-search">{copy.search}</Label>
              <Input
                id="graph-search"
                type="search"
                placeholder={copy.searchPlaceholder}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
            </div>
            <div className="flex min-w-0 flex-col gap-1">
              <Label htmlFor="graph-kind">{copy.kindFilter}</Label>
              <select
                id="graph-kind"
                className={NATIVE_SELECT}
                value={kind}
                onChange={(e) => setKind(e.target.value)}
              >
                <option value="">{copy.allKinds}</option>
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {copy.kind[k]}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" size="sm">
              {copy.search}
            </Button>
          </form>
          <div className="grid min-w-0 gap-6 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
            <div className={cn("min-w-0", selected && "hidden md:block")}>
              <EntityList
                key={workspaceId}
                workspaceId={workspaceId}
                q={q}
                kind={kind}
                selected={selected}
                onSelect={setSelected}
              />
            </div>
            <div className="min-w-0">
              {selected ? (
                <EntityTimeline
                  key={selected}
                  workspaceId={workspaceId}
                  entityId={selected}
                  onBack={() => setSelected(null)}
                />
              ) : (
                <p className="hidden text-sm text-muted-foreground md:block">{copy.selectHint}</p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
