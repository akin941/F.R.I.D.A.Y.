"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { LearningSubnav } from "@/components/memory/LearningSubnav";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson, apiPatchJson, apiPostJson, errorMessage } from "@/lib/api-client";
import { formatDateTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { TONE_PILL_CLASSES } from "@/lib/theme/status";
import { cn } from "@/lib/utils";
import type { ApiCoreBlock, ApiCoreBlockVersion, ApiSelfSources } from "@/types/api";

const copy = tr.learning.coreBlocks;

function blockPath(workspaceId: string, label: string, suffix = "") {
  return `/api/memory/core-blocks/${label}${suffix}?workspace=${encodeURIComponent(workspaceId)}`;
}

/** The `self` block's source lines (file:line anchors), read-only. */
function SelfAnchors() {
  const query = useQuery({
    queryKey: queryKeys.selfSources(),
    queryFn: () => apiGetJson<ApiSelfSources>("/api/memory/self-sources"),
  });
  return (
    <section className="flex flex-col gap-1" data-testid="self-anchors">
      <h4 className="text-sm font-medium">{copy.sources}</h4>
      {query.isPending ? (
        <Skeleton className="h-16 w-full" />
      ) : query.isError ? (
        <ErrorState error={query.error} title={copy.sourcesFailed} onRetry={() => void query.refetch()} />
      ) : (
        <ul className="flex flex-col gap-1 text-xs">
          {query.data.anchors.map((anchor, index) => (
            <li key={`${anchor.file}:${anchor.line}:${index}`} className="flex flex-col gap-0.5 rounded-md border px-2 py-1">
              <span className="flex flex-wrap gap-x-2 text-muted-foreground">
                <span>{copy.anchorGroup[anchor.group]}</span>
                {anchor.label ? <span>{anchor.label}</span> : null}
                <code className="font-mono break-all">
                  {anchor.file}
                  {anchor.line ? `:${anchor.line}` : ""}
                </code>
              </span>
              <span className="break-words">{anchor.text}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function VersionHistory({ block, workspaceId }: { block: ApiCoreBlock; workspaceId: string }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.coreBlockVersions(workspaceId, block.label),
    queryFn: () => apiGetJson<ApiCoreBlockVersion[]>(blockPath(workspaceId, block.label, "/versions")),
  });
  if (query.isPending) return <Skeleton className="h-16 w-full" />;
  if (query.isError) {
    return <ErrorState error={query.error} title={copy.historyFailed} onRetry={() => void query.refetch()} />;
  }
  if (query.data.length === 0) return <p className="text-xs text-muted-foreground">{copy.noHistory}</p>;
  return (
    <ol className="flex flex-col gap-1.5" data-testid={`core-block-history-${block.label}`}>
      {query.data.map((version) => {
        const current = version.version === block.version;
        return (
          <li key={version.version} className="flex flex-col gap-1 rounded-md border px-2 py-1.5 text-xs">
            <span className="flex flex-wrap items-center gap-x-2 text-muted-foreground">
              <span className="font-medium text-foreground">{copy.version(version.version)}</span>
              {current ? <Badge variant="outline">{copy.current}</Badge> : null}
              <span>
                {copy.updatedBy(copy.writer[version.updated_by_kind] ?? version.updated_by_kind, formatDateTime(version.created_at))}
              </span>
            </span>
            <pre className="text-xs break-words whitespace-pre-wrap">{version.content || copy.empty}</pre>
            {block.editable && !current ? (
              <ConfirmDialog
                trigger={
                  <Button size="sm" variant="outline" className="self-start">
                    {copy.rollback}
                  </Button>
                }
                title={copy.rollbackTitle(version.version)}
                description={copy.rollbackDescription}
                confirmLabel={copy.rollback}
                destructive={false}
                errorFallback={copy.rollbackFailed}
                onConfirm={async () => {
                  await apiPostJson(blockPath(workspaceId, block.label, "/rollback"), { version: version.version });
                  await queryClient.invalidateQueries({ queryKey: queryKeys.coreBlocks(workspaceId) });
                }}
              />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function BlockCard({ block, workspaceId }: { block: ApiCoreBlock; workspaceId: string }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(block.content);
  const [isPrivate, setIsPrivate] = useState(block.sensitivity === "private");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const over = content.length > block.char_budget;

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiPatchJson(blockPath(workspaceId, block.label), {
        content,
        sensitivity: isPrivate ? "private" : "normal",
        base_version: block.version,
      });
      setEditing(false);
      await queryClient.invalidateQueries({ queryKey: queryKeys.coreBlocks(workspaceId) });
    } catch (err) {
      setError(errorMessage(err, copy.saveFailed));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card data-testid={`core-block-${block.label}`} data-editable={block.editable}>
      <CardHeader className="gap-1">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <h3 className="font-medium">{copy.label[block.label]}</h3>
          <Badge variant="outline">{copy.version(block.version)}</Badge>
          {block.sensitivity === "private" ? (
            <Badge variant="outline" className={TONE_PILL_CLASSES.warning}>
              {copy.private}
            </Badge>
          ) : null}
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          {block.updated_by_kind && block.updated_at
            ? copy.updatedBy(copy.writer[block.updated_by_kind], formatDateTime(block.updated_at))
            : null}
          {block.updated_by_kind ? " · " : ""}
          {copy.chars(block.content.length, block.char_budget)}
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {editing ? (
          <form className="flex flex-col gap-2" onSubmit={save}>
            <Textarea
              aria-label={copy.label[block.label]}
              value={content}
              rows={6}
              onChange={(e) => setContent(e.target.value)}
              aria-invalid={over}
            />
            <p className={cn("text-xs", over ? "text-danger" : "text-muted-foreground")}>
              {copy.chars(content.length, block.char_budget)}
              {over ? ` — ${copy.overBudget}` : ""}
            </p>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={isPrivate} onChange={(e) => setIsPrivate(e.target.checked)} />
              {copy.private}
            </label>
            <div className="flex flex-wrap gap-2">
              <Button type="submit" size="sm" disabled={busy || over}>
                {busy ? copy.busy : copy.save}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
                  setEditing(false);
                  setContent(block.content);
                }}
              >
                {copy.cancel}
              </Button>
            </div>
          </form>
        ) : (
          <pre className="rounded-md bg-muted p-2 text-sm break-words whitespace-pre-wrap" data-testid={`core-block-content-${block.label}`}>
            {block.content || copy.empty}
          </pre>
        )}
        {error ? (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        ) : null}
        {block.editable ? null : <p className="text-xs text-muted-foreground">{copy.readOnly}</p>}
        <div className="flex flex-wrap gap-2">
          {block.editable && !editing ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setContent(block.content);
                setIsPrivate(block.sensitivity === "private");
                setEditing(true);
              }}
            >
              {copy.edit}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" aria-expanded={showHistory} onClick={() => setShowHistory((v) => !v)}>
            {showHistory ? copy.hideHistory : copy.history}
          </Button>
        </div>
        {block.label === "self" ? <SelfAnchors /> : null}
        {showHistory ? <VersionHistory block={block} workspaceId={workspaceId} /> : null}
      </CardContent>
    </Card>
  );
}

function BlocksForWorkspace({ workspaceId }: { workspaceId: string }) {
  const query = useQuery({
    queryKey: queryKeys.coreBlocks(workspaceId),
    queryFn: () =>
      apiGetJson<ApiCoreBlock[]>(`/api/memory/core-blocks?workspace=${encodeURIComponent(workspaceId)}`),
  });
  if (query.isPending) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  if (query.isError) {
    return <ErrorState error={query.error} title={copy.loadFailed} onRetry={() => void query.refetch()} />;
  }
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {query.data.map((block) => (
        // Re-keyed by version so a saved/rolled-back block resets its form.
        <BlockCard key={`${block.label}-${block.version}`} block={block} workspaceId={workspaceId} />
      ))}
    </div>
  );
}

/**
 * The core-block editor (Phase 8A D7): the active workspace's blocks with
 * their version history and rollback. `self` is read-only and shows the
 * snapshot lines it is assembled from (D8); the backend refuses any write
 * to it regardless.
 */
export function CoreBlocksView() {
  const { activeWorkspace } = useWorkspace();
  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-4 md:p-6")}>
      <PageHeader title={copy.title} description={copy.description} />
      <LearningSubnav />
      {activeWorkspace ? (
        <BlocksForWorkspace key={activeWorkspace.id} workspaceId={activeWorkspace.id} />
      ) : (
        <p className="text-sm text-muted-foreground">{tr.common.noWorkspace}</p>
      )}
    </div>
  );
}
