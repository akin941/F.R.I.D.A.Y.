"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Sunrise } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { LearningSubnav } from "@/components/memory/LearningSubnav";
import { useLearningDigest } from "@/components/memory/learning-queries";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { apiPostJson, errorMessage } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { TONE_PILL_CLASSES } from "@/lib/theme/status";
import { cn } from "@/lib/utils";
import type { ApiLearningDigest, ApiLearningItem } from "@/types/api";

const copy = tr.learning.card;

export function proposalPath(item: ApiLearningItem, action: string): string {
  return `/api/memory/learning/proposals/${item.kind}/${encodeURIComponent(item.id)}/${action}`;
}

function sourceLine(item: ApiLearningItem): string {
  const kind = item.source_kind ? tr.learning.sourceKind[item.source_kind] : "—";
  return copy.source(kind, formatDateTime(item.created_at));
}

/** The provenance quote is always rendered (D9): the owner's own words. */
function Quote({ quote }: { quote: string }) {
  if (!quote) {
    return (
      <p className={cn("rounded-md border px-3 py-2 text-xs", TONE_PILL_CLASSES.warning)} data-testid="quote-missing">
        {copy.quoteMissing}
      </p>
    );
  }
  return (
    <figure className="flex flex-col gap-1" data-testid="provenance-quote">
      <figcaption className="text-xs font-medium text-muted-foreground">{copy.quote}</figcaption>
      <blockquote className="border-l-2 border-border pl-3 text-sm break-words whitespace-pre-wrap italic">
        {quote}
      </blockquote>
    </figure>
  );
}

function useRemoveItems() {
  const queryClient = useQueryClient();
  return (ids: string[]) => {
    queryClient.setQueryData<ApiLearningDigest>(queryKeys.learningDigest(), (prev) => {
      if (!prev) return prev;
      const drop = new Set(ids);
      const workspaces = prev.workspaces
        .map((group) => ({ ...group, items: group.items.filter((i) => !drop.has(i.id)) }))
        .filter((group) => group.items.length > 0);
      return { ...prev, count: workspaces.reduce((n, g) => n + g.items.length, 0), workspaces };
    });
    void queryClient.invalidateQueries({ queryKey: queryKeys.learningDigest() });
  };
}

export function LearningItemCard({ item }: { item: ApiLearningItem }) {
  const removeItems = useRemoveItems();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(item.title);
  const [content, setContent] = useState(item.content);

  async function decide(action: "approve" | "reject" | "edit", body?: Record<string, string>) {
    setBusy(true);
    setError(null);
    try {
      // Memory approve/edit carry (optional) curated fields; core-block
      // actions carry no body.
      const payload = item.kind === "memory" && action !== "reject" ? (body ?? {}) : undefined;
      await apiPostJson(proposalPath(item, action), payload);
      if (action === "edit") {
        setEditing(false);
        queryClient.setQueryData<ApiLearningDigest>(queryKeys.learningDigest(), (prev) =>
          prev
            ? {
                ...prev,
                workspaces: prev.workspaces.map((g) => ({
                  ...g,
                  items: g.items.map((i) => (i.id === item.id ? { ...i, title, content } : i)),
                })),
              }
            : prev,
        );
      } else {
        removeItems([item.id]);
      }
    } catch (err) {
      setError(errorMessage(err, copy.actionFailed));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  const isCoreBlock = item.kind === "core-block";
  const heading = isCoreBlock ? copy.coreBlock(tr.learning.coreBlocks.label[item.label]) : item.title;

  return (
    <Card data-testid={`learning-item-${item.id}`} data-kind={item.kind}>
      <CardHeader className="gap-1">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <h3 className="min-w-0 font-medium break-words">{heading}</h3>
          {item.kind === "memory" ? (
            <Badge variant="outline">{tr.memory.type[item.memory_type]}</Badge>
          ) : null}
          {item.kind === "memory" && item.sensitivity === "private" ? (
            <Badge variant="outline" className={TONE_PILL_CLASSES.warning}>
              {tr.memory.sensitivity.private}
            </Badge>
          ) : null}
          {isCoreBlock && item.inferred ? <Badge variant="outline">{copy.inferred}</Badge> : null}
        </CardTitle>
        <p className="text-xs text-muted-foreground">{sourceLine(item)}</p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {editing && item.kind === "memory" ? (
          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void decide("edit", { title, content }).catch(() => undefined);
            }}
          >
            <Label htmlFor={`title-${item.id}`}>{copy.titleLabel}</Label>
            <Input id={`title-${item.id}`} value={title} onChange={(e) => setTitle(e.target.value)} />
            <Label htmlFor={`content-${item.id}`}>{copy.contentLabel}</Label>
            <Textarea
              id={`content-${item.id}`}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={4}
            />
            <div className="flex flex-wrap gap-2">
              <Button type="submit" size="sm" disabled={busy || !content.trim()}>
                {busy ? copy.busy : copy.saveOnly}
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setEditing(false)}>
                {tr.common.cancel}
              </Button>
            </div>
          </form>
        ) : isCoreBlock ? (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium text-muted-foreground">{copy.diff}</p>
            <pre className="overflow-x-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap break-words">
              {item.diff || item.content}
            </pre>
            <p className="text-xs text-muted-foreground">{copy.coreBlockNote}</p>
          </div>
        ) : (
          <p className="text-sm break-words whitespace-pre-wrap">{item.content}</p>
        )}

        <Quote quote={item.quote} />

        {error ? (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        ) : null}

        {!editing ? (
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={busy}
              onClick={() => void decide("approve").catch(() => undefined)}
            >
              {busy ? copy.busy : copy.approve}
            </Button>
            {item.kind === "memory" ? (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => setEditing(true)}>
                {copy.edit}
              </Button>
            ) : null}
            <ConfirmDialog
              trigger={
                <Button size="sm" variant="outline" disabled={busy}>
                  {copy.reject}
                </Button>
              }
              title={copy.rejectTitle}
              description={copy.rejectDescription(heading)}
              confirmLabel={copy.reject}
              busyLabel={copy.busy}
              errorFallback={copy.actionFailed}
              onConfirm={() => decide("reject")}
            />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** Bulk approval exists only for work_log items (d6); nothing else is offered it. */
function BulkWorkLog({ items }: { items: ApiLearningItem[] }) {
  const removeItems = useRemoveItems();
  const ids = items.filter((i) => i.kind === "memory" && i.bulk_approvable && i.memory_type === "work_log").map((i) => i.id);
  if (ids.length < 2) return null;
  return (
    <div className="flex flex-col gap-1 rounded-md border p-3" data-testid="bulk-work-log">
      <p className="text-sm font-medium">{copy.bulkHeading}</p>
      <p className="text-xs text-muted-foreground">{copy.bulkHint}</p>
      <ConfirmDialog
        trigger={
          <Button size="sm" variant="outline" className="self-start">
            {copy.bulkApprove(ids.length)}
          </Button>
        }
        title={copy.bulkTitle(ids.length)}
        description={copy.bulkDescription}
        confirmLabel={copy.approve}
        busyLabel={copy.busy}
        destructive={false}
        errorFallback={copy.bulkFailed}
        onConfirm={async () => {
          await apiPostJson("/api/memory/learning/bulk-approve", { ids });
          removeItems(ids);
        }}
      />
    </div>
  );
}

/**
 * The morning card (Phase 8A D10): last night's learned proposals grouped
 * by workspace, each with its source and the owner's own quoted words, and
 * per-item approve / edit / reject. Core-block proposals are approved one
 * at a time; only work_log items can be approved together.
 */
export function LearningCardView() {
  const query = useLearningDigest();
  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-4 md:p-6")}>
      <PageHeader title={copy.title} description={copy.description} />
      <LearningSubnav />
      {query.isPending ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : query.isError ? (
        <ErrorState error={query.error} title={copy.loadFailed} onRetry={() => void query.refetch()} />
      ) : query.data.count === 0 ? (
        <EmptyState icon={Sunrise} title={copy.empty} />
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            {copy.count(query.data.count)} · {copy.since(formatDateTime(query.data.since))}
          </p>
          {query.data.workspaces.map((group) => (
            <section
              key={group.workspace.id}
              className="flex flex-col gap-3"
              aria-labelledby={`learning-ws-${group.workspace.id}`}
              data-testid={`learning-workspace-${group.workspace.id}`}
            >
              <h2 id={`learning-ws-${group.workspace.id}`} className="text-lg font-semibold tracking-tight">
                {group.workspace.name}
              </h2>
              <BulkWorkLog items={group.items} />
              <div className="grid gap-3 lg:grid-cols-2">
                {group.items.map((item) => (
                  <LearningItemCard key={`${item.kind}-${item.id}`} item={item} />
                ))}
              </div>
            </section>
          ))}
        </>
      )}
    </div>
  );
}
