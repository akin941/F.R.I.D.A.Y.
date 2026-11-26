"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  AppWindow,
  CircleCheck,
  CircleDashed,
  CircleOff,
  Command,
  KeyRound,
  Monitor,
  ShieldOff,
  Terminal,
} from "lucide-react";
import { NODE_HUD_TOKENS, NODE_MIC_HUD_TOKENS } from "@/components/hud/HudPanels";
import { PLATFORM_LABELS, ROLE_LABELS } from "@/components/nodes/NodeLabelDialog";
import { formatDateTime, formatRelativeTime } from "@/lib/format/relative-time";
import type { HudNodePresence, HudNodes } from "@/lib/hud/events";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";
import type { ApiNodeRole } from "@/types/api";

const PANEL = "flex min-w-0 flex-col gap-2 rounded-xl border border-border/60 p-3 sm:p-4";
const HEADING = "text-xs font-semibold tracking-wide text-hud-caption-muted uppercase";

const STATUS_ICON: Record<HudNodePresence["status"], typeof CircleCheck> = {
  connected: CircleCheck,
  disconnected: CircleDashed,
  kill_switch: ShieldOff,
  revoked: CircleOff,
  credential_expiring: KeyRound,
};

const PLATFORM_ICON: Record<string, typeof Command> = {
  macos: Command,
  linux: Terminal,
  windows: AppWindow,
};

/** The HUD shows the per-node panel instead of the single line (D4). */
export function showNodesPanel(nodes: HudNodes | null | undefined): boolean {
  return Boolean(nodes && nodes.total > 1);
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role as ApiNodeRole] ?? role;
}

function NodeRow({ node }: { node: HudNodePresence }) {
  const copy = tr.hud.nodes;
  const health = tr.hud.health;
  const StatusIcon = STATUS_ICON[node.status] ?? CircleDashed;
  const PlatformIcon = PLATFORM_ICON[node.platform] ?? Monitor;
  const platform = PLATFORM_LABELS[node.platform] ?? node.platform;
  const voiceKnown = node.voice.state !== "unknown" && node.voice.state !== "off";
  return (
    <li>
      {/* A link per row: keyboard-reachable, opens the node's detail. */}
      <Link
        href={`/devices/nodes/${node.id}`}
        className="flex flex-col gap-0.5 rounded-md px-1 py-1 text-xs underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2"
        data-testid="hud-nodes-row"
        data-status={node.status}
        aria-label={copy.rowLabel(node.name, health.nodeStates[node.status] ?? node.status)}
      >
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="min-w-0 truncate font-medium text-hud-caption">{node.name}</span>
          {node.room ? (
            <span
              className="rounded-full border border-border/60 px-1.5 text-hud-caption-muted"
              data-testid="hud-nodes-room"
            >
              {node.room.name}
            </span>
          ) : null}
          <span className="inline-flex items-center gap-1 text-hud-caption-muted" data-testid="hud-nodes-platform">
            <PlatformIcon aria-hidden className="size-3.5 shrink-0" />
            {platform}
          </span>
        </span>
        {/* Status is an icon plus its word, never colour alone. */}
        <span
          className={cn("inline-flex items-center gap-1", NODE_HUD_TOKENS[node.status] ?? "text-hud-caption")}
          data-testid="hud-nodes-status"
        >
          <StatusIcon aria-hidden className="size-3.5 shrink-0" />
          {health.nodeStates[node.status] ?? node.status}
          {node.last_heartbeat_at ? (
            <span className="text-hud-caption-muted" title={formatDateTime(node.last_heartbeat_at)}>
              {" · "}
              {health.nodeHeartbeat} {formatRelativeTime(node.last_heartbeat_at)}
            </span>
          ) : null}
        </span>
        <span className="text-hud-caption-muted" data-testid="hud-nodes-counts">
          {roleLabel(node.role)} · {copy.counts(node.invocable_read_count, node.invocable_action_count)}
        </span>
        <span
          className={NODE_MIC_HUD_TOKENS[node.voice.state] ?? "text-hud-caption"}
          data-testid="hud-nodes-mic"
          data-state={node.voice.state}
        >
          {copy.mic}: {health.nodeMicStates[node.voice.state] ?? node.voice.state}
          {voiceKnown && node.voice.mode ? ` · ${health.nodeMicModes[node.voice.mode] ?? node.voice.mode}` : null}
        </span>
      </Link>
    </li>
  );
}

/**
 * The HUD "Düğümler" panel (Phase 13 D4): one row per non-revoked node --
 * name, room badge, platform icon with its label, status icon with its word,
 * last heartbeat, role, invocable read/action counts and the mic state.
 * Read-only: every row is a link to `/devices/nodes/<id>`. Only a node
 * connecting or disconnecting is announced (`aria-live="polite"`); a
 * heartbeat or count change is not.
 */
export function HudNodesPanel({ nodes }: { nodes: HudNodes }) {
  const copy = tr.hud.nodes;
  const previous = useRef<Map<string, boolean> | null>(null);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    const current = new Map(nodes.items.map((node) => [node.id, node.online]));
    const before = previous.current;
    previous.current = current;
    if (before === null) return;
    const changes = nodes.items
      .filter((node) => before.has(node.id) && before.get(node.id) !== node.online)
      .map((node) => (node.online ? copy.connectedNow(node.name) : copy.disconnectedNow(node.name)));
    // Announce a presence transition once, after it is rendered.
    if (changes.length > 0) setAnnouncement(changes.join(". "));
  }, [nodes, copy]);

  const extra = nodes.total - nodes.items.length;
  return (
    <section className={PANEL} aria-labelledby="hud-nodes" data-testid="hud-nodes">
      <h2 id="hud-nodes" className={cn(HEADING, "flex justify-between gap-2")}>
        <span>{copy.heading(nodes.online, nodes.total)}</span>
        <Link href="/devices" className="font-normal normal-case underline underline-offset-2">
          {tr.hud.health.nodeManage}
        </Link>
      </h2>
      <ul className="flex flex-col gap-1">
        {nodes.items.map((node) => (
          <NodeRow key={node.id} node={node} />
        ))}
      </ul>
      {extra > 0 ? <p className="text-xs text-hud-caption-muted">{copy.more(extra)}</p> : null}
      <p className="sr-only" aria-live="polite" data-testid="hud-nodes-announcement">
        {announcement}
      </p>
    </section>
  );
}
