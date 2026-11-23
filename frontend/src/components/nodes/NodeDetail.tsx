"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { NodeCapabilityList } from "@/components/nodes/NodesSection";
import {
  formatWhen,
  invocationStatusLabel,
  nodeStatusLabel,
  shortHash,
} from "@/components/nodes/labels";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { apiGetJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";
import type { ApiNodeDetail, ApiNodeInvocation, ApiNodeInvocationPage } from "@/types/api";

const copy = tr.nodes;
const cols = copy.logColumns;

/**
 * The node's invocation log (Phase 9 D11): the chain rows the actor can
 * see, newest first -- capability, status, workspace, duration, the args
 * hash and the output size. Never args or output content (D8): the API
 * carries none, and this view renders only the fields it names.
 */
export function NodeInvocationLog({ rows }: { rows: ApiNodeInvocation[] }) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">{copy.logEmpty}</p>;
  }
  return (
    <ul className="flex flex-col gap-2" data-testid="node-invocation-log">
      {rows.map((row) => (
        <li
          key={row.id}
          className="flex flex-col gap-1 rounded-lg border p-3 text-xs"
          data-testid="node-invocation"
          data-status={row.status}
        >
          <div className="flex flex-wrap items-center gap-2">
            <code className="break-all text-sm">{row.capability}</code>
            <StatusPill
              domain="nodeInvocation"
              status={row.status}
              label={
                invocationStatusLabel(row.status) + (row.refusal_code ? ` · ${row.refusal_code}` : "")
              }
            />
          </div>
          <dl className="grid grid-cols-1 gap-x-3 gap-y-0.5 text-muted-foreground sm:grid-cols-[auto_1fr]">
            <dt>{cols.when}</dt>
            <dd className="text-foreground">
              #{row.seq} · {formatWhen(row.created_at)}
            </dd>
            <dt>{cols.workspace}</dt>
            <dd className="text-foreground">{row.workspace_name}</dd>
            <dt>{cols.duration}</dt>
            <dd className="text-foreground">{copy.durationMs(row.duration_ms)}</dd>
            <dt>{cols.args}</dt>
            <dd className="font-mono text-foreground" title={row.args_sha256}>
              {shortHash(row.args_sha256)}
            </dd>
            <dt>{cols.output}</dt>
            <dd className="text-foreground" title={row.output_sha256 || undefined}>
              {row.output_sha256
                ? `${shortHash(row.output_sha256)} · ${copy.logOutput(row.output_bytes, row.truncated)}`
                : "—"}
              {row.dropped_unmapped > 0 ? ` · ${copy.logDropped(row.dropped_unmapped)}` : null}
            </dd>
          </dl>
        </li>
      ))}
    </ul>
  );
}

/** Route `/devices/nodes/[nodeId]`: the node's summary and invocation log. */
export function NodeDetailPage({ nodeId }: { nodeId: string }) {
  const [node, setNode] = useState<ApiNodeDetail | null>(null);
  const [rows, setRows] = useState<ApiNodeInvocation[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [logError, setLogError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [detail, page] = await Promise.all([
        apiGetJson<ApiNodeDetail>(`/api/nodes/${nodeId}`),
        apiGetJson<ApiNodeInvocationPage>(`/api/nodes/${nodeId}/invocations`),
      ]);
      setNode(detail);
      setRows(page.results);
      setCursor(page.next_cursor);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [nodeId]);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount, the standard data-fetching effect pattern */
  useEffect(() => {
    void load();
  }, [load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function loadMore() {
    if (cursor === null) return;
    setLoadingMore(true);
    setLogError(null);
    try {
      const page = await apiGetJson<ApiNodeInvocationPage>(
        `/api/nodes/${nodeId}/invocations?cursor=${cursor}`,
      );
      setRows((prev) => [...prev, ...page.results]);
      setCursor(page.next_cursor);
    } catch (err) {
      setLogError(err);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-4 sm:p-6")}>
      <div>
        <Link href="/devices" className="text-sm text-muted-foreground underline underline-offset-2">
          {copy.detailBack}
        </Link>
      </div>
      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : error || !node ? (
        <ErrorState error={error} title={copy.detailLoadFailed} onRetry={() => void load()} />
      ) : (
        <>
          <div className="flex flex-col gap-2">
            <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
              <span className="min-w-0 break-words">{node.name}</span>
              <StatusPill domain="node" status={node.status} label={nodeStatusLabel(node.status)} />
            </h1>
            <p className="text-sm text-muted-foreground">
              {copy.lastHeartbeat}: {formatWhen(node.last_heartbeat_at)} ·{" "}
              {copy.credentialAge(node.credential_age_days)} ·{" "}
              {copy.catalogue(node.catalogue_sha256, node.invocable_count, node.advertised_count)}
            </p>
          </div>
          <section className="flex flex-col gap-2" aria-labelledby="node-capabilities-heading">
            <h2 id="node-capabilities-heading" className="text-sm font-medium">
              {copy.capabilities}
            </h2>
            <NodeCapabilityList capabilities={node.capabilities} />
            <p className="text-xs text-muted-foreground">{copy.policyNote}</p>
          </section>
          <section className="flex flex-col gap-2" aria-labelledby="node-log-heading">
            <h2 id="node-log-heading" className="text-sm font-medium">
              {copy.logHeading}
            </h2>
            <p className="text-xs text-muted-foreground">{copy.logIntro}</p>
            <NodeInvocationLog rows={rows} />
            {logError ? <ErrorState error={logError} title={copy.logFailed} /> : null}
            {cursor !== null ? (
              <div>
                <Button variant="outline" size="sm" onClick={() => void loadMore()} disabled={loadingMore}>
                  {copy.logMore}
                </Button>
              </div>
            ) : null}
          </section>
        </>
      )}
    </div>
  );
}
