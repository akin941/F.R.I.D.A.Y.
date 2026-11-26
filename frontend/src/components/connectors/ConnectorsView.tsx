"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug } from "lucide-react";
import { HomeEntitiesEditor } from "@/components/connectors/HomeEntitiesEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson, apiPutJson } from "@/lib/api-client";
import { formatDateTime, formatRelativeTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { POLL_FAST_MS, pollWhile } from "@/lib/query/polling";
import { TONE_PILL_CLASSES } from "@/lib/theme/status";
import { cn } from "@/lib/utils";
import type { ApiConnectorBinding, ApiConnectorRecentRead } from "@/types/api";

const copy = tr.connectors;

/** An enabled binding whose pins are not checked yet: poll until the after-commit check lands. */
function awaitingPinCheck(bindings: ApiConnectorBinding[]): boolean {
  return bindings.some((b) => b.enabled && b.tools.some((t) => t.pin_status === "unchecked"));
}

export function useConnectorBindings(workspaceId: string) {
  return useQuery({
    queryKey: queryKeys.connectorBindings(workspaceId),
    queryFn: () =>
      apiGetJson<ApiConnectorBinding[]>(
        `/api/connectors/bindings?workspace=${encodeURIComponent(workspaceId)}`,
      ),
    refetchInterval: pollWhile<ApiConnectorBinding[]>((list) =>
      awaitingPinCheck(list) ? POLL_FAST_MS : null,
    ),
  });
}

function putBinding(workspaceId: string, connector: string, enabled: boolean) {
  return apiPutJson<ApiConnectorBinding>(
    `/api/connectors/bindings/${encodeURIComponent(connector)}`,
    { workspace: workspaceId, enabled },
  );
}

function BindingCard({ binding, workspaceId }: { binding: ApiConnectorBinding; workspaceId: string }) {
  const queryClient = useQueryClient();
  const personal = binding.data_class === "personal";
  const blocked = binding.status === "blocked";
  const name = binding.display_name;

  async function toggle() {
    const updated = await putBinding(workspaceId, binding.connector, !binding.enabled);
    queryClient.setQueryData<ApiConnectorBinding[]>(
      queryKeys.connectorBindings(workspaceId),
      (prev) => prev?.map((b) => (b.connector === updated.connector ? updated : b)),
    );
    void queryClient.invalidateQueries({ queryKey: queryKeys.connectorBindings(workspaceId) });
  }

  return (
    <Card data-testid={`connector-${binding.connector}`}>
      <CardHeader className="gap-1">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <h2 className="font-medium">{name}</h2>
          <StatusPill
            domain="pin"
            status={binding.enabled ? "ok" : "unchecked"}
            label={binding.enabled ? copy.enabled : copy.disabled}
          />
          <Badge variant="outline">{copy.kind[binding.kind] ?? binding.kind}</Badge>
          <Badge variant="outline" className={cn(personal && TONE_PILL_CLASSES.warning)}>
            {copy.dataClass[binding.data_class] ?? binding.data_class}
          </Badge>
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          <code className="font-mono">{binding.connector}</code> · manifest v{binding.manifest_version}
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {blocked ? (
          <p className="text-sm text-muted-foreground">{copy.blocked}</p>
        ) : !binding.bindable ? (
          <p className="text-sm text-muted-foreground">{copy.notBindable}</p>
        ) : (
          <ConfirmDialog
            trigger={
              <Button
                size="sm"
                variant={binding.enabled ? "outline" : "default"}
                className="self-start"
              >
                {binding.enabled ? copy.disable : copy.enable}
              </Button>
            }
            title={binding.enabled ? copy.disableTitle(name) : copy.enableTitle(name)}
            description={
              binding.enabled ? copy.disableDescription(name) : copy.enableDescription(name, personal)
            }
            confirmLabel={binding.enabled ? copy.disable : copy.enable}
            busyLabel={copy.busy}
            destructive={binding.enabled}
            errorFallback={copy.toggleFailed}
            onConfirm={toggle}
          />
        )}

        <section className="flex flex-col gap-1">
          <h3 className="text-sm font-medium text-muted-foreground">{copy.tools}</h3>
          <ul className="flex flex-col gap-1.5">
            {binding.tools.map((tool) => (
              <li
                key={tool.name}
                data-testid={`tool-${tool.name}`}
                className="flex flex-col gap-0.5 rounded-md border px-2.5 py-1.5 text-sm sm:flex-row sm:items-center sm:gap-2"
              >
                <code className="font-mono text-xs break-all">{tool.name}</code>
                <span className="text-xs text-muted-foreground">
                  {copy.toolKind[tool.kind] ?? tool.kind}
                </span>
                {binding.enabled ? (
                  <StatusPill
                    domain="pin"
                    status={tool.pin_status}
                    label={tr.status.pin[tool.pin_status] ?? tool.pin_status}
                    className="self-start sm:ml-auto"
                  />
                ) : null}
              </li>
            ))}
          </ul>
          {binding.tools.some((t) => t.pin_status === "mismatch" || t.pin_status === "missing") ? (
            <p className="text-xs text-danger">{copy.pinNote}</p>
          ) : null}
        </section>

        {binding.enabled ? (
          <section className="flex flex-col gap-0.5 text-xs text-muted-foreground">
            <h3 className="text-sm font-medium">{copy.health}</h3>
            {binding.health.checked_at ? (
              <>
                <p title={formatDateTime(binding.health.checked_at)}>
                  {copy.checkedAt(formatRelativeTime(binding.health.checked_at))}
                </p>
                {binding.health.server_name ? (
                  <p>{copy.server(binding.health.server_name, binding.health.server_version)}</p>
                ) : null}
                {binding.health.protocol_version ? (
                  <p>{copy.protocol(binding.health.protocol_version)}</p>
                ) : null}
              </>
            ) : (
              <p>{copy.neverChecked}</p>
            )}
            {binding.health.error ? (
              <p className="text-danger">{copy.healthError(binding.health.error)}</p>
            ) : null}
          </section>
        ) : null}
      </CardContent>
    </Card>
  );
}

function RecentReads({ workspaceId }: { workspaceId: string }) {
  const query = useQuery({
    queryKey: queryKeys.connectorReads(workspaceId),
    queryFn: () =>
      apiGetJson<ApiConnectorRecentRead[]>(
        `/api/connectors/reads?workspace=${encodeURIComponent(workspaceId)}`,
      ),
  });
  return (
    <section className="flex flex-col gap-2" aria-labelledby="recent-reads">
      <h2 id="recent-reads" className="text-lg font-semibold tracking-tight">
        {copy.recentReads}
      </h2>
      <p className="text-sm text-muted-foreground">{copy.recentReadsHint}</p>
      {query.isPending ? (
        <Skeleton className="h-16 w-full" />
      ) : query.isError ? (
        <ErrorState error={query.error} title={copy.recentReadsFailed} onRetry={() => void query.refetch()} />
      ) : query.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">{copy.recentReadsEmpty}</p>
      ) : (
        <ul className="flex flex-col divide-y rounded-md border text-sm">
          {query.data.map((read) => (
            <li
              key={read.id}
              data-testid={`read-${read.id}`}
              className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-2"
            >
              <span className="text-xs text-muted-foreground" title={formatDateTime(read.created_at)}>
                {formatRelativeTime(read.created_at)}
              </span>
              <code className="font-mono text-xs break-all">{read.tool}</code>
              <span className={cn("text-xs", read.outcome !== "ok" && "text-danger")}>
                {copy.readOutcome[read.outcome] ?? read.outcome}
              </span>
              <span className="text-xs text-muted-foreground">
                {copy.chars(read.size)}
                {read.truncated ? ` (${copy.truncated})` : ""}
                {read.duration_ms != null ? ` · ${copy.ms(read.duration_ms)}` : ""}
                {` · ${tr.chat.channel[read.channel] ?? read.channel}`}
              </span>
              {read.data_class === "personal" ? (
                <Badge variant="outline" className={TONE_PILL_CLASSES.warning}>
                  {copy.personalRead}
                </Badge>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ConnectorsForWorkspace({ workspaceId }: { workspaceId: string }) {
  const query = useConnectorBindings(workspaceId);
  return (
    <>
      {query.isPending ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : query.isError ? (
        <ErrorState error={query.error} title={copy.loadFailed} onRetry={() => void query.refetch()} />
      ) : query.data.length === 0 ? (
        <EmptyState icon={Plug} title={copy.empty} />
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {query.data.map((binding) => (
            <BindingCard key={binding.connector} binding={binding} workspaceId={workspaceId} />
          ))}
        </div>
      )}
      {query.data?.some((b) => b.connector === "home_assistant" && b.bindable) ? (
        <HomeEntitiesEditor workspaceId={workspaceId} />
      ) : null}
      <RecentReads workspaceId={workspaceId} />
    </>
  );
}

/**
 * Settings → "Bağlantılar" (Phase 5 branch 9): the active workspace's
 * connectors -- binding state, per-tool pin status, last health check --
 * and the recent reads from the audit trail. Switching a connector on or
 * off happens only from its confirmation dialog; owner + trusted web
 * session are enforced by the backend.
 */
export function ConnectorsView() {
  const { activeWorkspace } = useWorkspace();
  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-4 md:p-6")}>
      <PageHeader title={copy.title} description={copy.description} />
      {activeWorkspace ? (
        <ConnectorsForWorkspace key={activeWorkspace.id} workspaceId={activeWorkspace.id} />
      ) : (
        <p className="text-sm text-muted-foreground">{tr.common.noWorkspace}</p>
      )}
    </div>
  );
}
