"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseZap } from "lucide-react";
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
import { LearningSubnav } from "@/components/memory/LearningSubnav";
import { usePersonalWorkspace } from "@/components/memory/learning-queries";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiDelete, apiGetJson, apiPatchJson, apiPostJson, errorMessage } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { TONE_PILL_CLASSES } from "@/lib/theme/status";
import { cn } from "@/lib/utils";
import type {
  ApiForgetResult,
  ApiIngestionAudit,
  ApiIngestionSource,
  ApiMappingRule,
  ApiScrubTerm,
  MappingRuleType,
} from "@/types/api";

const copy = tr.learning.ingestion;

export const NATIVE_SELECT =
  "h-8 w-full min-w-0 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

const RULE_TYPES: MappingRuleType[] = ["repo", "path_prefix", "calendar_binding"];

/** The rule types a source accepts (Phase 8B branch 7, D12): the Obsidian
 * inbox takes `vault_prefix` only, and no other source takes it -- the
 * backend refuses any other pairing with a 400. */
export function ruleTypesFor(kind: ApiIngestionSource["kind"]): MappingRuleType[] {
  return kind === "obsidian_inbox" ? ["vault_prefix"] : RULE_TYPES;
}

function defaultRuleType(kind: ApiIngestionSource["kind"]): MappingRuleType {
  if (kind === "obsidian_inbox") return "vault_prefix";
  if (kind === "calendar") return "calendar_binding";
  if (kind === "claude_code_hooks") return "path_prefix";
  return "repo";
}

export function sourceName(source: Pick<ApiIngestionSource, "kind">): string {
  return tr.learning.sourceKind[source.kind] ?? source.kind;
}

function useOwnedWorkspaces() {
  const { workspaces } = useWorkspace();
  return workspaces.filter((w) => w.role === "owner");
}

function invalidateSources(queryClient: ReturnType<typeof useQueryClient>, workspaceId: string) {
  return queryClient.invalidateQueries({ queryKey: queryKeys.ingestionSources(workspaceId) });
}

function SourceStatus({ source }: { source: ApiIngestionSource }) {
  return (
    <span className="flex flex-wrap gap-1.5">
      <Badge
        variant="outline"
        className={source.enabled ? TONE_PILL_CLASSES.success : TONE_PILL_CLASSES.neutral}
      >
        {source.enabled ? copy.enabled : copy.disabled}
      </Badge>
      {source.paused ? (
        <Badge variant="outline" className={TONE_PILL_CLASSES.warning}>
          {copy.paused}
        </Badge>
      ) : null}
      {source.forgotten_at ? (
        <Badge variant="outline" className={TONE_PILL_CLASSES.danger}>
          {copy.forgotten}
        </Badge>
      ) : null}
    </span>
  );
}

function CountersTable({ audit }: { audit: ApiIngestionAudit }) {
  const h = copy.counterHeaders;
  if (audit.counters.length === 0) return <p className="text-sm text-muted-foreground">{copy.noCounters}</p>;
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full min-w-[32rem] text-left text-xs" data-testid="ingestion-counters">
        <thead className="bg-muted text-muted-foreground">
          <tr>
            <th className="px-2 py-1 font-medium">{h.day}</th>
            <th className="px-2 py-1 font-medium">{h.stored}</th>
            <th className="px-2 py-1 font-medium">{h.duplicate}</th>
            <th className="px-2 py-1 font-medium">{h.dropped}</th>
            <th className="px-2 py-1 font-medium">{h.tokens}</th>
            <th className="px-2 py-1 font-medium">{h.proposals}</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {[...audit.counters].reverse().map((c) => (
            <tr key={c.day} className="border-t">
              <td className="px-2 py-1">{c.day}</td>
              <td className="px-2 py-1">{c.stored}</td>
              <td className="px-2 py-1">{c.duplicate}</td>
              <td className="px-2 py-1">
                {c.dropped_unmapped} / {c.dropped_paused}
              </td>
              <td className="px-2 py-1">{c.tokens_spent}</td>
              <td className="px-2 py-1">
                {c.proposals_created} / {c.proposals_accepted} / {c.proposals_rejected}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RulesSection({ source, personalId }: { source: ApiIngestionSource; personalId: string }) {
  const queryClient = useQueryClient();
  const owned = useOwnedWorkspaces();
  const rules = useQuery({
    queryKey: queryKeys.mappingRules(personalId, source.id),
    queryFn: () => apiGetJson<ApiMappingRule[]>(`/api/memory/ingestion/sources/${source.id}/rules`),
  });
  const [ruleType, setRuleType] = useState<MappingRuleType>(defaultRuleType(source.kind));
  const [pattern, setPattern] = useState("");
  const [target, setTarget] = useState(personalId);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const names = new Map(owned.map((w) => [w.id, w.name]));

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: queryKeys.mappingRules(personalId, source.id) });
  }

  async function add(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiPostJson(`/api/memory/ingestion/sources/${source.id}/rules`, {
        rule_type: ruleType,
        pattern: pattern.trim(),
        target_workspace: target,
      });
      setPattern("");
      await refresh();
    } catch (err) {
      setError(errorMessage(err, copy.ruleFailed));
    } finally {
      setBusy(false);
    }
  }

  async function toggle(rule: ApiMappingRule) {
    setError(null);
    try {
      await apiPatchJson(`/api/memory/ingestion/rules/${rule.id}`, { enabled: !rule.enabled });
      await refresh();
    } catch (err) {
      setError(errorMessage(err, copy.ruleFailed));
    }
  }

  return (
    <section className="flex flex-col gap-2" data-testid="ingestion-rules">
      <h4 className="text-sm font-medium">{copy.rules}</h4>
      {rules.isPending ? (
        <Skeleton className="h-8 w-full" />
      ) : rules.isError ? (
        <ErrorState error={rules.error} title={copy.ruleFailed} onRetry={() => void rules.refetch()} />
      ) : rules.data.length === 0 ? (
        <p className="text-xs text-muted-foreground">{copy.noRules}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {rules.data.map((rule) => (
            <li key={rule.id} className="flex flex-wrap items-center gap-2 rounded-md border px-2 py-1 text-xs">
              <span className="text-muted-foreground">{copy.ruleType[rule.rule_type]}</span>
              <code className="min-w-0 font-mono break-all">{rule.pattern}</code>
              <span>→ {names.get(rule.target_workspace) ?? rule.target_workspace}</span>
              <label className="ml-auto flex items-center gap-1">
                <input type="checkbox" checked={rule.enabled} onChange={() => void toggle(rule)} />
                {copy.ruleEnabled}
              </label>
            </li>
          ))}
        </ul>
      )}
      <form className="grid gap-2 sm:grid-cols-[auto_1fr_auto_auto] sm:items-end" onSubmit={add}>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`rule-type-${source.id}`}>{copy.ruleTypeLabel}</Label>
          <select
            id={`rule-type-${source.id}`}
            className={NATIVE_SELECT}
            value={ruleType}
            onChange={(e) => setRuleType(e.target.value as MappingRuleType)}
          >
            {ruleTypesFor(source.kind).map((type) => (
              <option key={type} value={type}>
                {copy.ruleType[type]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor={`rule-pattern-${source.id}`}>{copy.rulePattern}</Label>
          <Input id={`rule-pattern-${source.id}`} value={pattern} onChange={(e) => setPattern(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`rule-target-${source.id}`}>{copy.ruleTarget}</Label>
          <select
            id={`rule-target-${source.id}`}
            className={NATIVE_SELECT}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          >
            {owned.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </div>
        <Button type="submit" size="sm" disabled={busy || !pattern.trim()}>
          {copy.addRule}
        </Button>
      </form>
      {error ? (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function SourceDetail({ source, personalId }: { source: ApiIngestionSource; personalId: string }) {
  const queryClient = useQueryClient();
  const audit = useQuery({
    queryKey: queryKeys.ingestionSource(personalId, source.id),
    queryFn: () => apiGetJson<ApiIngestionAudit>(`/api/memory/ingestion/sources/${source.id}`),
  });
  const [budget, setBudget] = useState(String(source.daily_token_budget));
  const [budgetError, setBudgetError] = useState<string | null>(null);

  if (audit.isPending) return <Skeleton className="h-40 w-full" />;
  if (audit.isError) {
    return <ErrorState error={audit.error} title={copy.detailFailed} onRetry={() => void audit.refetch()} />;
  }
  const data = audit.data;
  const name = sourceName(source);
  const preview = data.forget_preview;
  const hits = Object.entries(data.scrubber_hits);
  const rate = data.acceptance.rate;

  async function refreshAll() {
    await Promise.all([
      invalidateSources(queryClient, personalId),
      queryClient.invalidateQueries({ queryKey: queryKeys.ingestionSource(personalId, source.id) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.learningDigest() }),
    ]);
  }

  async function saveBudget(event: React.FormEvent) {
    event.preventDefault();
    setBudgetError(null);
    try {
      await apiPatchJson(`/api/memory/ingestion/sources/${source.id}`, {
        daily_token_budget: Number(budget),
      });
      await refreshAll();
    } catch (err) {
      setBudgetError(errorMessage(err, copy.toggleFailed));
    }
  }

  return (
    <div className="flex flex-col gap-4" data-testid={`ingestion-detail-${source.id}`}>
      <p className="text-sm">
        {rate === null ? copy.acceptanceNone : copy.acceptance(`${Math.round(rate * 100)}%`)}
        {" · "}
        {copy.budget(data.tokens_spent_today, data.daily_token_budget)}
      </p>

      <section className="flex flex-col gap-1">
        <h4 className="text-sm font-medium">{copy.counters}</h4>
        <CountersTable audit={data} />
      </section>

      <section className="flex flex-col gap-1">
        <h4 className="text-sm font-medium">{copy.scrubberHits}</h4>
        {hits.length === 0 ? (
          <p className="text-xs text-muted-foreground">{copy.noScrubberHits}</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5" data-testid="scrubber-hits">
            {hits.map(([type, count]) => (
              <li key={type}>
                <Badge variant="outline">
                  {type}: {count}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-1">
        <h4 className="text-sm font-medium">{copy.lastItems}</h4>
        {data.episodes.length === 0 ? (
          <p className="text-xs text-muted-foreground">{copy.noItems}</p>
        ) : (
          <ul className="flex flex-col gap-1.5" data-testid="ingestion-episodes">
            {data.episodes.map((episode) => (
              <li key={episode.id} className="flex flex-col gap-0.5 rounded-md border px-2 py-1.5 text-xs">
                <span className="flex flex-wrap gap-x-2 text-muted-foreground">
                  <span>{formatDateTime(episode.occurred_at)}</span>
                  <span>{episode.workspace_name}</span>
                  <code className="font-mono">{episode.kind}</code>
                  <span>{episode.extracted ? copy.extracted : copy.notExtracted}</span>
                  <span>{copy.expires(formatDateTime(episode.expires_at))}</span>
                </span>
                {episode.owner_text ? (
                  <p className="break-words whitespace-pre-wrap">
                    <span className="font-medium">{copy.ownerText}: </span>
                    {episode.owner_text}
                  </p>
                ) : null}
                {episode.context_text ? (
                  <p className="break-words whitespace-pre-wrap text-muted-foreground">
                    <span className="font-medium">{copy.contextText}: </span>
                    {episode.context_text}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-1">
        <h4 className="text-sm font-medium">{copy.unmapped}</h4>
        <p className="text-xs text-muted-foreground">{copy.unmappedHint}</p>
        {data.unmapped_keys.length === 0 ? (
          <p className="text-xs text-muted-foreground">{copy.noUnmapped}</p>
        ) : (
          <ul className="flex flex-col gap-0.5 text-xs" data-testid="unmapped-keys">
            {data.unmapped_keys.map((key) => (
              <li key={key.hash}>
                <code className="font-mono break-all">{key.short}</code>
              </li>
            ))}
          </ul>
        )}
      </section>

      <RulesSection source={source} personalId={personalId} />

      <form className="flex flex-wrap items-end gap-2" onSubmit={saveBudget}>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`budget-${source.id}`}>{copy.budgetLabel}</Label>
          <Input
            id={`budget-${source.id}`}
            type="number"
            min={0}
            className="w-40"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
        </div>
        <Button type="submit" size="sm" variant="outline">
          {copy.saveBudget}
        </Button>
        {budgetError ? (
          <p role="alert" className="text-xs text-danger">
            {budgetError}
          </p>
        ) : null}
      </form>

      <div className="flex flex-wrap gap-2">
        {source.forgotten_at === null ? (
          <ConfirmDialog
            trigger={
              <Button size="sm" variant="destructive">
                {copy.forget}
              </Button>
            }
            title={copy.forgetTitle(name)}
            description={copy.forgetDescription(
              name,
              preview.episodes,
              preview.memories,
              preview.core_block_proposals,
            )}
            confirmLabel={copy.forget}
            errorFallback={copy.forgetFailed}
            onConfirm={async () => {
              await apiPostJson<ApiForgetResult>(`/api/memory/ingestion/sources/${source.id}/forget`);
              await refreshAll();
            }}
          />
        ) : null}
        {source.restorable ? (
          <ConfirmDialog
            trigger={
              <Button size="sm" variant="outline">
                {copy.restore}
              </Button>
            }
            title={copy.restoreTitle(name)}
            description={copy.restoreDescription}
            confirmLabel={copy.restore}
            destructive={false}
            errorFallback={copy.restoreFailed}
            onConfirm={async () => {
              await apiPostJson<ApiForgetResult>(`/api/memory/ingestion/sources/${source.id}/restore`);
              await refreshAll();
            }}
          />
        ) : null}
      </div>
    </div>
  );
}

function SourceCard({ source, personalId }: { source: ApiIngestionSource; personalId: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const today = source.today;

  async function patch(body: Record<string, boolean>) {
    setError(null);
    try {
      await apiPatchJson(`/api/memory/ingestion/sources/${source.id}`, body);
      await invalidateSources(queryClient, personalId);
    } catch (err) {
      setError(errorMessage(err, copy.toggleFailed));
    }
  }

  return (
    <Card data-testid={`ingestion-source-${source.kind}`}>
      <CardHeader className="gap-1">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <h3 className="font-medium">{sourceName(source)}</h3>
          <SourceStatus source={source} />
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          {copy.today(today?.stored ?? 0, (today?.dropped_unmapped ?? 0) + (today?.dropped_paused ?? 0))}
          {" · "}
          {copy.budget(today?.tokens_spent ?? 0, source.daily_token_budget)}
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant={source.enabled ? "outline" : "default"} onClick={() => void patch({ enabled: !source.enabled })}>
            {source.enabled ? copy.disable : copy.enable}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void patch({ paused: !source.paused })}>
            {source.paused ? copy.resume : copy.pause}
          </Button>
          <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? copy.hideDetails : copy.details}
          </Button>
        </div>
        {error ? (
          <p role="alert" className="text-xs text-danger">
            {error}
          </p>
        ) : null}
        {open ? <SourceDetail source={source} personalId={personalId} /> : null}
      </CardContent>
    </Card>
  );
}

function ScrubTerms() {
  const owned = useOwnedWorkspaces();
  const queryClient = useQueryClient();
  const [workspaceId, setWorkspaceId] = useState(owned[0]?.id ?? "");
  const [term, setTerm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const terms = useQuery({
    queryKey: queryKeys.scrubTerms(workspaceId),
    queryFn: () =>
      apiGetJson<ApiScrubTerm[]>(`/api/memory/ingestion/scrub-terms?workspace=${encodeURIComponent(workspaceId)}`),
    enabled: Boolean(workspaceId),
  });

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: queryKeys.scrubTerms(workspaceId) });
  }

  async function add(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await apiPostJson("/api/memory/ingestion/scrub-terms", { workspace: workspaceId, term: term.trim() });
      setTerm("");
      await refresh();
    } catch (err) {
      setError(errorMessage(err, copy.termFailed));
    }
  }

  return (
    <section className="flex flex-col gap-2" aria-labelledby="scrub-terms" data-testid="scrub-terms">
      <h2 id="scrub-terms" className="text-lg font-semibold tracking-tight">
        {copy.scrubTerms}
      </h2>
      <p className="text-sm text-muted-foreground">{copy.scrubTermsHint}</p>
      <select
        aria-label={copy.termWorkspace}
        className={cn(NATIVE_SELECT, "sm:w-64")}
        value={workspaceId}
        onChange={(e) => setWorkspaceId(e.target.value)}
      >
        {owned.map((w) => (
          <option key={w.id} value={w.id}>
            {w.name}
          </option>
        ))}
      </select>
      {!workspaceId ? null : terms.isPending ? (
        <Skeleton className="h-8 w-full" />
      ) : terms.isError ? (
        <ErrorState error={terms.error} title={copy.termFailed} onRetry={() => void terms.refetch()} />
      ) : terms.data.length === 0 ? (
        <p className="text-xs text-muted-foreground">{copy.noScrubTerms}</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {terms.data.map((t) => (
            <li key={t.id} className="flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs">
              <span className="break-all">{t.term}</span>
              <ConfirmDialog
                trigger={
                  <Button size="sm" variant="ghost" aria-label={copy.removeTerm(t.term)}>
                    ×
                  </Button>
                }
                title={copy.removeTermTitle}
                description={copy.removeTermDescription(t.term)}
                confirmLabel={copy.removeTermTitle}
                errorFallback={copy.termFailed}
                onConfirm={async () => {
                  await apiDelete(`/api/memory/ingestion/scrub-terms/${t.id}`);
                  await refresh();
                }}
              />
            </li>
          ))}
        </ul>
      )}
      <form className="flex flex-wrap items-end gap-2" onSubmit={add}>
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor="scrub-term">{copy.termLabel}</Label>
          <Input id="scrub-term" value={term} onChange={(e) => setTerm(e.target.value)} />
        </div>
        <Button type="submit" size="sm" disabled={!term.trim() || !workspaceId}>
          {copy.addTerm}
        </Button>
      </form>
      {error ? (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function SourcesForWorkspace({ personalId }: { personalId: string }) {
  const sources = useQuery({
    queryKey: queryKeys.ingestionSources(personalId),
    queryFn: () =>
      apiGetJson<ApiIngestionSource[]>(`/api/memory/ingestion/sources?workspace=${encodeURIComponent(personalId)}`),
  });
  if (sources.isPending) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (sources.isError) {
    return <ErrorState error={sources.error} title={copy.loadFailed} onRetry={() => void sources.refetch()} />;
  }
  if (sources.data.length === 0) return <EmptyState icon={DatabaseZap} title={copy.empty} />;
  return (
    <div className="flex flex-col gap-3">
      {sources.data.map((source) => (
        <SourceCard key={source.id} source={source} personalId={personalId} />
      ))}
    </div>
  );
}

/**
 * `/memory/ingestion` (Phase 8A D11): every learning source of the owner --
 * enable / pause, counters, the last stored (scrubbed) items, unmapped
 * keys, mapping rules, scrub terms -- and forget / restore behind a
 * confirmation that names the source and the counts (ADR-009).
 */
export function IngestionView() {
  const personal = usePersonalWorkspace();
  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-4 md:p-6")}>
      <PageHeader title={copy.title} description={copy.description} />
      <LearningSubnav />
      {personal ? (
        <>
          <SourcesForWorkspace key={personal.id} personalId={personal.id} />
          <ScrubTerms />
        </>
      ) : (
        <p className="text-sm text-muted-foreground">{copy.noPersonal}</p>
      )}
    </div>
  );
}
