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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiGetJson } from "@/lib/api-client";
import { fetchNodeAudit } from "@/lib/api/home";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";
import type {
  ApiNodeAuditRow,
  ApiNodeDetail,
  ApiNodeInvocation,
  ApiNodeInvocationPage,
} from "@/types/api";

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

const AUDIT_ACTIONS: Record<string, string> = {
  "nodes.node.paired": "Eşleştirildi",
  "accounts.node.enrolled": "Kimlik kaydedildi",
  "nodes.relabelled": "Etiketler değişti",
  "accounts.device.revoked": "İptal edildi",
  "accounts.node.rotated": "Kimlik bilgisi yenilendi",
  "accounts.node.rotation_refused": "Kimlik yenileme reddedildi",
  "accounts.node.kill_switch.engaged": "Acil durdurma açıldı",
  "accounts.node.kill_switch.released": "Acil durdurma kapatıldı",
  "nodes.refused": "Çağrı reddedildi",
  "node_voice_token.minted": "Ses kimliği verildi",
  "accounts.voice_live_mute.engaged": "Mikrofon susturuldu",
  "accounts.voice_live_mute.released": "Mikrofon susturması kaldırıldı",
};

function auditDetailText(details: Record<string, unknown>): string {
  return Object.entries(details)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : String(value)}`)
    .join(" · ");
}

/**
 * The node's audit (Phase 13 D8): its chain rows and its audit events
 * (paired, relabelled, revoked, kill switch, rotation, refusals, mutes),
 * newest first. Codes, keys and hashes only -- the API carries no args,
 * output or address, and this view renders only the fields it names.
 */
export function NodeAuditLog({ rows }: { rows: ApiNodeAuditRow[] }) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">Denetim kaydı yok.</p>;
  }
  return (
    <ul className="flex flex-col gap-2" data-testid="node-audit-log">
      {rows.map((row) =>
        row.kind === "invocation" ? (
          <li
            key={row.id}
            className="flex flex-wrap items-center gap-2 rounded-lg border p-3 text-xs"
            data-testid="node-audit-invocation"
          >
            <code className="break-all">{row.capability}</code>
            <StatusPill
              domain="nodeInvocation"
              status={row.status}
              label={invocationStatusLabel(row.status) + (row.refusal_code ? ` · ${row.refusal_code}` : "")}
            />
            <span className="text-muted-foreground">
              #{row.seq} · {formatWhen(row.created_at)} · {row.workspace_name}
            </span>
          </li>
        ) : (
          <li
            key={row.id}
            className="flex flex-col gap-1 rounded-lg border p-3 text-xs"
            data-testid="node-audit-event"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">{AUDIT_ACTIONS[row.action] ?? row.action}</span>
              <span className="text-muted-foreground">{formatWhen(row.created_at)}</span>
            </div>
            {Object.keys(row.details).length > 0 ? (
              <span className="break-all text-muted-foreground">{auditDetailText(row.details)}</span>
            ) : null}
          </li>
        ),
      )}
    </ul>
  );
}

function NodeAuditTab({ nodeId }: { nodeId: string }) {
  const [rows, setRows] = useState<ApiNodeAuditRow[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const loadPage = useCallback(
    async (before: string | null) => {
      setBusy(true);
      setError(null);
      try {
        const page = await fetchNodeAudit(nodeId, before);
        setRows((prev) => (before && prev ? [...prev, ...page.results] : page.results));
        setCursor(page.next_cursor);
      } catch (err) {
        setError(err);
      } finally {
        setBusy(false);
      }
    },
    [nodeId],
  );

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount */
  useEffect(() => {
    void loadPage(null);
  }, [loadPage]);
  /* eslint-enable react-hooks/set-state-in-effect */

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-muted-foreground">
        Bu düğümün çağrıları ve olayları; yalnızca kodlar ve özetler, hiçbir içerik yok.
      </p>
      {rows === null && !error ? <Skeleton className="h-24 w-full" /> : null}
      {rows ? <NodeAuditLog rows={rows} /> : null}
      {error ? <ErrorState error={error} title="Denetim kaydı yüklenemedi." /> : null}
      {cursor !== null ? (
        <div>
          <Button variant="outline" size="sm" onClick={() => void loadPage(cursor)} disabled={busy}>
            {copy.logMore}
          </Button>
        </div>
      ) : null}
    </div>
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
          <Tabs defaultValue="log">
            <TabsList>
              <TabsTrigger value="log">{copy.logHeading}</TabsTrigger>
              <TabsTrigger value="audit">Denetim</TabsTrigger>
            </TabsList>
            <TabsContent value="log">
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
            </TabsContent>
            <TabsContent value="audit">
              <NodeAuditTab nodeId={nodeId} />
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}
