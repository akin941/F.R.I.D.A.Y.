"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { NodeLabelDialog, PLATFORM_LABELS, ROLE_LABELS } from "@/components/nodes/NodeLabelDialog";
import { useOptionalWorkspace } from "@/contexts/workspace-context";
import { ApiError, apiDelete, apiGetJson, apiPostJson } from "@/lib/api-client";
import { fetchRooms } from "@/lib/api/home";
import { tr } from "@/lib/i18n/tr";
import type {
  ApiNodeCapability,
  ApiNodeDetail,
  ApiNodeKillSwitch,
  ApiNodeSummary,
  ApiPairingCode,
  ApiRoom,
} from "@/types/api";
import {
  capabilityAvailabilityText,
  formatWhen,
  nodeStatusLabel,
} from "@/components/nodes/labels";

const copy = tr.nodes;

/**
 * A node pairing code: shown exactly once, cleared on "Tamam" and never
 * re-fetchable (the backend stores only its hash).
 */
function NodePairingCode({ code, onDismiss }: { code: ApiPairingCode; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);
  const pin = code.kind === "pin";
  return (
    <Alert data-testid="node-pairing-code">
      <AlertTitle>{pin ? copy.codeTitlePin : copy.codeTitleStrong}</AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <code className="break-all rounded-md bg-muted px-2 py-1 text-base tracking-wider">
          {code.code}
        </code>
        <span className="text-xs text-muted-foreground">
          {copy.codeHint(formatWhen(code.expires_at), pin)}
        </span>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => {
              navigator.clipboard
                .writeText(code.code)
                .then(() => setCopied(true))
                .catch(() => setCopied(false));
            }}
          >
            {copied ? copy.copied : copy.copy}
          </Button>
          <Button type="button" size="sm" onClick={onDismiss}>
            {copy.done}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

/** The snapshot entries as read-only text: no input of any kind (D11). */
export function NodeCapabilityList({ capabilities }: { capabilities: ApiNodeCapability[] }) {
  if (capabilities.length === 0) {
    return <p className="text-xs text-muted-foreground">{copy.capabilitiesNone}</p>;
  }
  return (
    <ul className="flex flex-col gap-0.5 text-xs" data-testid="node-capabilities">
      {capabilities.map((entry) => (
        <li
          key={`${entry.name}@${entry.version}`}
          className="flex flex-wrap items-baseline gap-x-2"
          data-invocable={entry.invocable}
        >
          <code className="break-all">{entry.name}</code>
          {entry.class ? (
            <span className="text-muted-foreground">
              ({copy.capabilityClasses[entry.class] ?? entry.class})
            </span>
          ) : null}
          <span className={entry.invocable ? "text-foreground" : "text-muted-foreground"}>
            {capabilityAvailabilityText(entry)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function NodeCard({
  node,
  rooms,
  onChanged,
}: {
  node: ApiNodeDetail;
  rooms: ApiRoom[];
  onChanged: (node: ApiNodeSummary) => void;
}) {
  const revoked = node.status === "revoked" && node.revoked_at !== null;

  async function handleRevoke() {
    onChanged(await apiPostJson<ApiNodeSummary>(`/api/nodes/${node.id}/revoke`));
  }

  const labels = [
    node.room ? node.room.name : null,
    node.role ? ROLE_LABELS[node.role] : null,
    node.platform ? (PLATFORM_LABELS[node.platform] ?? node.platform) : null,
  ].filter(Boolean);

  return (
    <Card data-testid="node-card" data-status={node.status}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <span className="min-w-0 break-words">{node.name}</span>
          <StatusPill domain="node" status={node.status} label={nodeStatusLabel(node.status)} />
        </CardTitle>
        {labels.length > 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="node-labels">
            {labels.join(" · ")}
            {node.aliases && node.aliases.length > 0 ? ` · takma adlar: ${node.aliases.join(", ")}` : null}
          </p>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <dl className="grid grid-cols-1 gap-x-3 gap-y-1 text-xs text-muted-foreground sm:grid-cols-[auto_1fr]">
          <dt>{copy.lastHeartbeat}</dt>
          <dd className="text-foreground">{formatWhen(node.last_heartbeat_at)}</dd>
          <dt>{copy.paired}</dt>
          <dd className="text-foreground">{formatWhen(node.paired_at)}</dd>
          <dt>{copy.credentialAge(node.credential_age_days)}</dt>
          <dd className="text-foreground">
            {copy.credentialExpires}: {formatWhen(node.credential_expires_at)}
          </dd>
          {node.node_version ? (
            <>
              <dt>{copy.version}</dt>
              <dd className="text-foreground">
                {node.node_version}
                {node.os_version ? ` · ${node.os_version}` : null}
              </dd>
            </>
          ) : null}
        </dl>
        <p className="text-xs text-muted-foreground">
          {copy.catalogue(node.catalogue_sha256, node.invocable_count, node.advertised_count)}
          {node.spool_count > 0 ? ` · ${copy.spool(node.spool_count)}` : null}
        </p>
        <div className="flex flex-col gap-1">
          <h4 className="text-xs font-medium">{copy.capabilities}</h4>
          <NodeCapabilityList capabilities={node.capabilities} />
          <p className="text-xs text-muted-foreground">{copy.policyNote}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href={`/devices/nodes/${node.id}`}>{copy.invocations}</Link>
          </Button>
          {node.revoked_at ? null : (
            <NodeLabelDialog node={node} rooms={rooms} onSaved={onChanged} />
          )}
          {revoked || node.revoked_at ? null : (
            <ConfirmDialog
              trigger={
                <Button variant="outline" size="sm">
                  {copy.revoke}
                </Button>
              }
              title={copy.revokeTitle}
              description={copy.revokeBody(node.name)}
              confirmLabel={copy.revokeConfirm}
              busyLabel={copy.revoking}
              errorFallback={copy.revokeFailed}
              onConfirm={handleRevoke}
            />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function KillSwitch({
  state,
  onChanged,
}: {
  state: ApiNodeKillSwitch;
  onChanged: (state: ApiNodeKillSwitch) => void;
}) {
  async function engage() {
    onChanged(await apiPostJson<ApiNodeKillSwitch>("/api/nodes/kill-switch"));
  }

  async function release() {
    await apiDelete("/api/nodes/kill-switch");
    onChanged(await apiGetJson<ApiNodeKillSwitch>("/api/nodes/kill-switch"));
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3" data-testid="node-kill-switch">
      <h3 className="text-sm font-medium">{copy.killSwitch}</h3>
      <p className={state.engaged ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>
        {state.engaged ? copy.killSwitchOn(formatWhen(state.engaged_at)) : copy.killSwitchOff}
      </p>
      <div>
        {state.engaged ? (
          <ConfirmDialog
            trigger={
              <Button variant="outline" size="sm">
                {copy.killSwitchRelease}
              </Button>
            }
            title={copy.killSwitchReleaseTitle}
            description={copy.killSwitchReleaseBody}
            confirmLabel={copy.killSwitchReleaseConfirm}
            busyLabel={copy.killSwitchBusy}
            destructive={false}
            errorFallback={copy.killSwitchFailed}
            onConfirm={release}
          />
        ) : (
          <ConfirmDialog
            trigger={
              <Button variant="destructive" size="sm">
                {copy.killSwitchEngage}
              </Button>
            }
            title={copy.killSwitchEngageTitle}
            description={copy.killSwitchEngageBody}
            confirmLabel={copy.killSwitchEngageConfirm}
            busyLabel={copy.killSwitchBusy}
            errorFallback={copy.killSwitchFailed}
            onConfirm={engage}
          />
        )}
      </div>
    </div>
  );
}

/**
 * `/devices` "Mac düğümleri" (Phase 9 D11): pair a Mac (a node code shown
 * once), the node list with status, last heartbeat, credential age and
 * capability availability as read-only text, revoke (naming the node) and
 * the owner's kill switch -- each behind a confirmation. Nothing here reads
 * or writes node policy: grants live on the Mac (ADR-013 decision 5).
 */
export function NodesSection() {
  const [nodes, setNodes] = useState<ApiNodeDetail[]>([]);
  const [killSwitch, setKillSwitch] = useState<ApiNodeKillSwitch | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pairingCode, setPairingCode] = useState<ApiPairingCode | null>(null);
  const [busy, setBusy] = useState(false);
  // Phase 13 D2: the personal workspace's rooms for the label dialog
  // (best effort: without them the dialog offers "Oda yok" only).
  const [rooms, setRooms] = useState<ApiRoom[]>([]);
  const workspaceContext = useOptionalWorkspace();
  const personalId =
    workspaceContext?.workspaces.find((workspace) => workspace.type === "personal")?.id ?? null;

  useEffect(() => {
    if (!personalId) return;
    let live = true;
    fetchRooms(personalId)
      .then((list) => {
        if (live) setRooms(list.results);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [personalId]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, state] = await Promise.all([
        apiGetJson<ApiNodeSummary[]>("/api/nodes"),
        apiGetJson<ApiNodeKillSwitch>("/api/nodes/kill-switch"),
      ]);
      const details = await Promise.all(
        list.map((node) => apiGetJson<ApiNodeDetail>(`/api/nodes/${node.id}`)),
      );
      setNodes(details);
      setKillSwitch(state);
    } catch {
      setError(copy.loadFailed);
    } finally {
      setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount, the standard data-fetching effect pattern */
  useEffect(() => {
    void load();
  }, [load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function issueCode(kind: "pin" | "strong") {
    setBusy(true);
    setError(null);
    setPairingCode(null);
    try {
      setPairingCode(await apiPostJson<ApiPairingCode>("/api/nodes/pairing-codes", { kind }));
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409 ? copy.pairKillSwitch : copy.pairFailed);
    } finally {
      setBusy(false);
    }
  }

  function handleChanged(updated: ApiNodeSummary) {
    setNodes((prev) =>
      prev.map((node) => (node.id === updated.id ? { ...node, ...updated } : node)),
    );
  }

  function handleKillSwitch(state: ApiNodeKillSwitch) {
    setKillSwitch(state);
    // Every node's status follows the switch: re-read the list.
    void load();
  }

  const engaged = Boolean(killSwitch?.engaged);

  return (
    <section className="flex flex-col gap-3" aria-labelledby="nodes-heading" data-testid="nodes-section">
      <div>
        <h2 id="nodes-heading" className="text-lg font-semibold tracking-tight">
          {copy.heading}
        </h2>
        <p className="text-sm text-muted-foreground">{copy.intro}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void issueCode("pin")} disabled={busy || engaged}>
          {copy.pairPin}
        </Button>
        <Button variant="outline" onClick={() => void issueCode("strong")} disabled={busy || engaged}>
          {copy.pairStrong}
        </Button>
      </div>
      {engaged ? <p className="text-xs text-muted-foreground">{copy.pairKillSwitch}</p> : null}

      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {pairingCode ? (
        <NodePairingCode
          key={pairingCode.code}
          code={pairingCode}
          onDismiss={() => setPairingCode(null)}
        />
      ) : null}

      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <>
          {killSwitch ? <KillSwitch state={killSwitch} onChanged={handleKillSwitch} /> : null}
          {nodes.length === 0 && !error ? (
            <p className="text-sm text-muted-foreground">{copy.empty}</p>
          ) : (
            <div className="flex flex-col gap-3">
              {nodes.map((node) => (
                <NodeCard key={node.id} node={node} rooms={rooms} onChanged={handleChanged} />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
