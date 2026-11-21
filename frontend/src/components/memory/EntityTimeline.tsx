"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { apiDelete, apiGetJson, apiPostJson, errorMessage } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { TONE_PILL_CLASSES } from "@/lib/theme/status";
import type { ApiGraphEntityDetail, ApiGraphFact, GraphEntityKind } from "@/types/api";

const copy = tr.learning.graph;

const GRAPHIFY_KINDS = new Set<GraphEntityKind>(["code_module", "code_class", "code_function"]);

/** The end of a picked calendar day: "as of 2026-03-01" includes that whole day. */
export function asOfInstant(date: string): string {
  return `${date}T23:59:59`;
}

function day(value: string): string {
  return value.slice(0, 10);
}

/** Splits an unfiltered timeline into current, superseded history and pending proposals. */
export function splitTimeline(facts: ApiGraphFact[]) {
  const live = (f: ApiGraphFact) => f.status === "approved" || f.status === "system";
  return {
    current: facts.filter((f) => live(f) && f.valid_to === null),
    history: facts.filter((f) => live(f) && f.valid_to !== null).reverse(),
    pending: facts.filter((f) => f.status === "pending_approval"),
  };
}

function objectLabel(fact: ApiGraphFact): string {
  return fact.object_entity ? fact.object_entity.name : fact.object_text;
}

/**
 * Arrow keys / Home / End move focus between the timeline's items (each is
 * also a normal tab stop), so a long history is walkable from the keyboard.
 */
function onListKeyDown(event: React.KeyboardEvent<HTMLElement>) {
  const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
  if (!keys.includes(event.key)) return;
  const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("[data-timeline-item]"));
  const index = items.indexOf(document.activeElement as HTMLElement);
  if (index === -1) return;
  event.preventDefault();
  const next =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? items.length - 1
        : Math.min(items.length - 1, Math.max(0, index + (event.key === "ArrowDown" ? 1 : -1)));
  items[next]?.focus();
}

function FactEditForm({
  fact,
  onDone,
  onCancel,
}: {
  fact: ApiGraphFact;
  onDone: () => Promise<void>;
  onCancel: () => void;
}) {
  const [statement, setStatement] = useState(fact.statement);
  const [value, setValue] = useState(objectLabel(fact));
  const [validFrom, setValidFrom] = useState(new Date().toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const base = `fact-edit-${fact.id}`;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const object = fact.object_entity
      ? { object_kind: fact.object_entity.kind, object_name: value.trim() }
      : { object_text: value.trim() };
    try {
      // The owner's correction is a supersede: the old version closes at
      // `valid_from` and stays readable in the history (D2).
      await apiPostJson(`/api/memory/graph/facts/${fact.id}/supersede`, {
        valid_from: `${validFrom}T00:00:00`,
        statement: statement.trim(),
        ...object,
      });
      await onDone();
    } catch (err) {
      setError(errorMessage(err, copy.editFailed));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="flex flex-col gap-2 rounded-md border p-2" onSubmit={submit} aria-label={copy.editTitle}>
      <p className="text-xs text-muted-foreground">{copy.editHint}</p>
      <div className="flex flex-col gap-1">
        <Label htmlFor={`${base}-statement`}>{copy.statementLabel}</Label>
        <Input id={`${base}-statement`} value={statement} onChange={(e) => setStatement(e.target.value)} />
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor={`${base}-value`}>{copy.objectLabel}</Label>
          <Input id={`${base}-value`} value={value} onChange={(e) => setValue(e.target.value)} />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor={`${base}-from`}>{copy.validFromLabel}</Label>
          <Input
            id={`${base}-from`}
            type="date"
            value={validFrom}
            onChange={(e) => setValidFrom(e.target.value)}
          />
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={busy || !value.trim() || !validFrom}>
          {busy ? copy.busy : copy.save}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          {copy.cancel}
        </Button>
      </div>
      {error ? (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      ) : null}
    </form>
  );
}

function FactItem({
  fact,
  editable,
  onChanged,
  onTrashed,
}: {
  fact: ApiGraphFact;
  editable: boolean;
  onChanged: () => Promise<void>;
  onTrashed: (fact: ApiGraphFact) => void;
}) {
  const [editing, setEditing] = useState(false);
  const current = fact.valid_to === null && fact.status === "approved";
  return (
    <li
      tabIndex={0}
      data-timeline-item
      data-testid={`fact-${fact.id}`}
      className="flex flex-col gap-1 rounded-md border px-2 py-1.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <p className="break-words">{fact.statement || `${fact.subject.name} ${copy.predicate[fact.predicate]} ${objectLabel(fact)}`}</p>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span>{copy.predicate[fact.predicate] ?? fact.predicate}</span>
        <span>
          {fact.valid_to
            ? copy.validRange(day(fact.valid_from), day(fact.valid_to))
            : copy.validFrom(day(fact.valid_from))}
        </span>
        <Badge variant="outline" className={fact.status === "pending_approval" ? TONE_PILL_CLASSES.warning : TONE_PILL_CLASSES.neutral}>
          {copy.status[fact.status]}
        </Badge>
        {fact.sensitivity === "private" ? (
          <Badge variant="outline" className={TONE_PILL_CLASSES.info}>
            {copy.private}
          </Badge>
        ) : null}
        {fact.origin === "learned" ? <span>{copy.confidence(fact.confidence)}</span> : null}
      </p>
      {fact.provenance_quote ? (
        <blockquote className="border-l-2 pl-2 text-xs break-words text-muted-foreground">
          <span className="font-medium">{copy.quote}: </span>
          {fact.provenance_quote}
        </blockquote>
      ) : null}
      {editable && current && !editing ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            {copy.edit}
          </Button>
          <ConfirmDialog
            trigger={
              <Button size="sm" variant="ghost">
                {copy.trash}
              </Button>
            }
            title={copy.trashTitle}
            description={copy.trashDescription(fact.statement)}
            confirmLabel={copy.trash}
            errorFallback={copy.trashFailed}
            onConfirm={async () => {
              await apiDelete(`/api/memory/graph/facts/${fact.id}`);
              onTrashed(fact);
              await onChanged();
            }}
          />
        </div>
      ) : null}
      {editing ? (
        <FactEditForm
          fact={fact}
          onCancel={() => setEditing(false)}
          onDone={async () => {
            setEditing(false);
            await onChanged();
          }}
        />
      ) : null}
    </li>
  );
}

function FactList({
  facts,
  empty,
  label,
  editable,
  onChanged,
  onTrashed,
}: {
  facts: ApiGraphFact[];
  empty: string;
  label: string;
  editable: boolean;
  onChanged: () => Promise<void>;
  onTrashed: (fact: ApiGraphFact) => void;
}) {
  if (facts.length === 0) return <p className="text-xs text-muted-foreground">{empty}</p>;
  return (
    <ol aria-label={label} className="flex flex-col gap-1.5" onKeyDown={onListKeyDown}>
      {facts.map((fact) => (
        <FactItem key={fact.id} fact={fact} editable={editable} onChanged={onChanged} onTrashed={onTrashed} />
      ))}
    </ol>
  );
}

/**
 * One entity's page on `/memory/graph` (Phase 8B branch 9): its current
 * facts, the superseded history and pending proposals, or -- with the "as
 * of" date -- only the facts valid then. Learned facts can be corrected
 * (a supersede, never an in-place edit) and trashed (reversible, ADR-009);
 * Graphify code entities are read-only and show their snapshot's sha.
 */
export function EntityTimeline({
  workspaceId,
  entityId,
  onBack,
}: {
  workspaceId: string;
  entityId: string;
  onBack?: () => void;
}) {
  const queryClient = useQueryClient();
  const [asOf, setAsOf] = useState("");
  const [notice, setNotice] = useState<{ text: string; restore: () => Promise<void> } | null>(null);
  const [noticeError, setNoticeError] = useState<string | null>(null);
  // After an entity trash its detail is gone (404): keep a restore notice.
  const [trashedEntity, setTrashedEntity] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: queryKeys.graphEntity(workspaceId, entityId),
    queryFn: () => apiGetJson<ApiGraphEntityDetail>(`/api/memory/graph/entities/${entityId}`),
  });
  const facts = useQuery({
    queryKey: queryKeys.graphEntityFacts(workspaceId, entityId, asOf),
    queryFn: () =>
      apiGetJson<ApiGraphFact[]>(
        `/api/memory/graph/entities/${entityId}/facts${asOf ? `?as_of=${encodeURIComponent(asOfInstant(asOf))}` : ""}`,
      ),
  });

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: queryKeys.graph(workspaceId) });
  }

  if (trashedEntity !== null) {
    return (
      <div role="status" className="flex flex-wrap items-center gap-2 rounded-md border px-2 py-1.5 text-sm" data-testid="entity-trashed">
        <span className="min-w-0 break-words">{trashedEntity}</span>
        <Button
          size="sm"
          variant="outline"
          onClick={async () => {
            setNoticeError(null);
            try {
              await apiPostJson(`/api/memory/graph/entities/${entityId}/restore`);
              setTrashedEntity(null);
              await refresh();
            } catch (err) {
              setNoticeError(errorMessage(err, copy.restoreFailed));
            }
          }}
        >
          {copy.restore}
        </Button>
        {noticeError ? (
          <span role="alert" className="text-xs text-danger">
            {noticeError}
          </span>
        ) : null}
      </div>
    );
  }
  if (detail.isPending) return <Skeleton className="h-48 w-full" />;
  if (detail.isError) {
    return <ErrorState error={detail.error} title={copy.detailFailed} onRetry={() => void detail.refetch()} />;
  }
  const { entity, fact_count: factCount, code_graph: snapshot } = detail.data;
  const readOnly = entity.origin === "graphify" || GRAPHIFY_KINDS.has(entity.kind);

  async function restoreAction(path: string) {
    setNoticeError(null);
    try {
      await apiPostJson(path);
      setNotice(null);
      await refresh();
    } catch (err) {
      setNoticeError(errorMessage(err, copy.restoreFailed));
    }
  }

  function onFactTrashed(fact: ApiGraphFact) {
    setNotice({
      text: copy.trashed(fact.statement),
      restore: () => restoreAction(`/api/memory/graph/facts/${fact.id}/restore`),
    });
  }

  const timeline = facts.data ? splitTimeline(facts.data) : null;

  return (
    <article className="flex min-w-0 flex-col gap-4" aria-labelledby="entity-heading" data-testid="entity-timeline">
      {onBack ? (
        <Button size="sm" variant="ghost" className="self-start md:hidden" onClick={onBack}>
          {copy.back}
        </Button>
      ) : null}
      <header className="flex flex-col gap-1">
        <h2 id="entity-heading" className="text-lg font-semibold tracking-tight break-words">
          {entity.name}
        </h2>
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <span>{copy.kind[entity.kind] ?? entity.kind}</span>
          {readOnly ? (
            <Badge variant="outline" className={TONE_PILL_CLASSES.neutral}>
              {copy.graphify}
            </Badge>
          ) : null}
          {entity.is_private ? (
            <Badge variant="outline" className={TONE_PILL_CLASSES.info}>
              {copy.private}
            </Badge>
          ) : null}
        </p>
        {entity.aliases.length > 0 ? (
          <p className="text-xs text-muted-foreground">{copy.aliases(entity.aliases.join(", "))}</p>
        ) : null}
        {readOnly ? (
          <div className="flex flex-col gap-0.5 text-xs" data-testid="entity-readonly">
            <p>{copy.readOnly}</p>
            {snapshot ? (
              <p className="text-muted-foreground">
                {copy.snapshot(
                  snapshot.commit_sha.slice(0, 12),
                  formatDateTime(snapshot.committed_at ?? snapshot.imported_at ?? ""),
                )}
                {snapshot.retired_at ? ` · ${copy.snapshotRetired}` : null}
              </p>
            ) : null}
          </div>
        ) : null}
      </header>

      {notice ? (
        <div role="status" className="flex flex-wrap items-center gap-2 rounded-md border px-2 py-1.5 text-sm">
          <span className="min-w-0 break-words">{notice.text}</span>
          <Button size="sm" variant="outline" onClick={() => void notice.restore()}>
            {copy.restore}
          </Button>
          {noticeError ? (
            <span role="alert" className="text-xs text-danger">
              {noticeError}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor={`as-of-${entityId}`}>{copy.asOf}</Label>
          <Input
            id={`as-of-${entityId}`}
            type="date"
            className="w-44"
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
            aria-describedby={`as-of-hint-${entityId}`}
          />
        </div>
        {asOf ? (
          <Button size="sm" variant="ghost" onClick={() => setAsOf("")}>
            {copy.clearAsOf}
          </Button>
        ) : null}
        <p id={`as-of-hint-${entityId}`} className="w-full text-xs text-muted-foreground">
          {copy.asOfHint}
        </p>
      </div>

      {facts.isPending ? (
        <Skeleton className="h-24 w-full" />
      ) : facts.isError ? (
        <ErrorState error={facts.error} title={copy.factsFailed} onRetry={() => void facts.refetch()} />
      ) : asOf ? (
        <section className="flex flex-col gap-1.5" data-testid="facts-as-of">
          <h3 className="text-sm font-medium">{copy.asOfHeading(asOf)}</h3>
          <FactList
            facts={facts.data.filter((f) => f.status !== "pending_approval")}
            empty={copy.noneAsOf}
            label={copy.asOfHeading(asOf)}
            editable={!readOnly}
            onChanged={refresh}
            onTrashed={onFactTrashed}
          />
        </section>
      ) : timeline ? (
        <>
          <section className="flex flex-col gap-1.5" data-testid="facts-current">
            <h3 className="text-sm font-medium">{copy.current}</h3>
            <FactList
              facts={timeline.current}
              empty={copy.noCurrent}
              label={copy.current}
              editable={!readOnly}
              onChanged={refresh}
              onTrashed={onFactTrashed}
            />
          </section>
          <section className="flex flex-col gap-1.5" data-testid="facts-history">
            <h3 className="text-sm font-medium">{copy.history}</h3>
            <FactList
              facts={timeline.history}
              empty={copy.noHistory}
              label={copy.history}
              editable={false}
              onChanged={refresh}
              onTrashed={onFactTrashed}
            />
          </section>
          {timeline.pending.length > 0 ? (
            <section className="flex flex-col gap-1.5" data-testid="facts-pending">
              <h3 className="text-sm font-medium">{copy.pending}</h3>
              <p className="text-xs text-muted-foreground">
                {copy.pendingHint}{" "}
                <Link href="/memory/learning" className="underline underline-offset-2">
                  {copy.openCard}
                </Link>
              </p>
              <FactList
                facts={timeline.pending}
                empty=""
                label={copy.pending}
                editable={false}
                onChanged={refresh}
                onTrashed={onFactTrashed}
              />
            </section>
          ) : null}
        </>
      ) : null}

      {!readOnly ? (
        <div>
          <ConfirmDialog
            trigger={
              <Button size="sm" variant="destructive">
                {copy.trashEntity}
              </Button>
            }
            title={copy.trashEntityTitle(entity.name)}
            description={copy.trashEntityDescription(entity.name, factCount)}
            confirmLabel={copy.trashEntity}
            errorFallback={copy.trashFailed}
            onConfirm={async () => {
              await apiDelete(`/api/memory/graph/entities/${entity.id}`);
              setNotice(null);
              setTrashedEntity(copy.entityTrashed(entity.name, factCount));
              await refresh();
            }}
          />
        </div>
      ) : null}
    </article>
  );
}
