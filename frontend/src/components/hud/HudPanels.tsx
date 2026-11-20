"use client";

import Link from "next/link";
import { AlertTriangle, CircleCheck, CircleDashed, CircleOff, CircleX } from "lucide-react";
import { formatDateTime, formatRelativeTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";
import type {
  HudApprovals,
  HudCost,
  HudHealth,
  HudProviderStatus,
  HudStreamStatus,
  HudTasks,
} from "@/lib/hud/events";
import { phaseCaption } from "@/lib/hud/states";
import type { VoicePresence } from "@/lib/voice/presence";

/**
 * The HUD panels (Phase 6 A8). Read-only views of the event stream and the
 * voice presence: captions, today's tasks, approvals (deep links to
 * `/approvals/<id>` -- never a decide control, D8), live cost and health.
 */

const PANEL = "flex min-w-0 flex-col gap-2 rounded-xl border border-border/60 p-3 sm:p-4";
const HEADING = "text-xs font-semibold tracking-wide text-hud-caption-muted uppercase";

function approvalTitle(tool: string): string {
  if (tr.approvals.toolTitles[tool]) return tr.approvals.toolTitles[tool];
  const connector = tool.match(/^mcp_([^_]+)_(.+)$/);
  return connector ? tr.approvals.connectorWriteTitle(connector[1]!, connector[2]!) : tool;
}

export function CaptionsPanel({ presence }: { presence: VoicePresence }) {
  const caption = phaseCaption(presence.phase);
  const hasText = Boolean(presence.heard || presence.reply);
  return (
    <section className={PANEL} aria-labelledby="hud-captions" data-testid="hud-captions">
      <h2 id="hud-captions" className={HEADING}>
        {tr.hud.captions.heading}
      </h2>
      {/* Captions are always available (A8): announced politely. */}
      <div aria-live="polite" className="flex flex-col gap-1.5 text-sm text-hud-caption">
        {presence.heard ? (
          <p data-testid="hud-caption-heard">
            <span className="text-hud-caption-muted">{tr.hud.captions.heard}: </span>
            {presence.heard}
          </p>
        ) : null}
        {caption ? (
          <p className="text-hud-caption-muted italic" data-testid="hud-caption-phase">
            {tr.hud.captions[caption]}
          </p>
        ) : null}
        {presence.reply ? (
          <p data-testid="hud-caption-reply">
            <span className="text-hud-caption-muted">{tr.hud.captions.reply}: </span>
            {presence.reply}
          </p>
        ) : null}
        {!hasText && !caption ? <p className="text-hud-caption-muted">{tr.hud.captions.empty}</p> : null}
      </div>
    </section>
  );
}

export function TasksPanel({ tasks }: { tasks: HudTasks | null }) {
  const items = tasks?.items ?? [];
  const extra = (tasks?.open_count ?? 0) - items.length;
  return (
    <section className={PANEL} aria-labelledby="hud-tasks" data-testid="hud-tasks">
      <h2 id="hud-tasks" className={HEADING}>
        {tr.hud.tasks.heading}
      </h2>
      {items.length === 0 ? (
        <p className="text-sm text-hud-caption-muted">{tr.hud.tasks.empty}</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm text-hud-caption">
          {items.map((task) => (
            <li key={task.id} className="flex items-baseline gap-2">
              <span className="min-w-0 flex-1 truncate">{task.title}</span>
              {task.overdue ? (
                <span className="shrink-0 text-xs text-hud-error">{tr.hud.tasks.overdue}</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <p className="flex justify-between text-xs text-hud-caption-muted">
        <span>{extra > 0 ? tr.hud.tasks.more(extra) : null}</span>
        <Link href="/today" className="underline underline-offset-2">
          {tr.hud.tasks.all}
        </Link>
      </p>
    </section>
  );
}

export function ApprovalsPanel({ approvals }: { approvals: HudApprovals | null }) {
  const items = approvals?.items ?? [];
  const extra = (approvals?.pending_count ?? 0) - items.length;
  return (
    <section className={PANEL} aria-labelledby="hud-approvals" data-testid="hud-approvals">
      <h2 id="hud-approvals" className={HEADING}>
        {tr.hud.approvals.heading}
        {approvals && approvals.pending_count > 0 ? ` (${approvals.pending_count})` : null}
      </h2>
      {approvals && approvals.tools_running > 0 ? (
        <p className="text-xs text-hud-tool-running" data-testid="hud-tools-running">
          {tr.hud.approvals.running(approvals.tools_running)}
        </p>
      ) : null}
      {items.length === 0 ? (
        <p className="text-sm text-hud-caption-muted">{tr.hud.approvals.empty}</p>
      ) : (
        <ul className="flex flex-col gap-1.5 text-sm">
          {items.map((item) => (
            <li key={item.id}>
              {/* D8: a deep link to the decision route; the HUD never decides. */}
              <Link
                href={`/approvals/${item.id}`}
                className="flex flex-col rounded-md px-1 py-0.5 text-hud-awaiting-approval underline-offset-2 hover:underline"
                data-testid="hud-approval-link"
              >
                <span className="truncate">{approvalTitle(item.tool_name)}</span>
                <span className="text-xs text-hud-caption-muted" title={formatDateTime(item.created_at)}>
                  {formatRelativeTime(item.created_at)} · {tr.hud.approvals.open}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {extra > 0 ? (
        <Link href="/approvals" className="text-xs text-hud-caption-muted underline underline-offset-2">
          {tr.hud.approvals.more(extra)}
        </Link>
      ) : null}
    </section>
  );
}

export function CostPanel({ cost }: { cost: HudCost | null }) {
  return (
    <section className={PANEL} aria-labelledby="hud-cost" data-testid="hud-cost">
      <h2 id="hud-cost" className={HEADING}>
        {tr.hud.cost.heading}
      </h2>
      <p className="text-2xl font-semibold text-hud-caption tabular-nums" data-testid="hud-cost-amount">
        {tr.hud.cost.amount(cost ? Number(cost.estimated_cost).toFixed(4) : "0.0000")}
      </p>
      <ul className="flex flex-col gap-0.5 text-xs text-hud-caption-muted">
        <li>{tr.hud.cost.calls(cost?.calls ?? 0)}</li>
        {cost && cost.failed_calls > 0 ? <li>{tr.hud.cost.failed(cost.failed_calls)}</li> : null}
        <li>{tr.hud.cost.tokens(cost?.input_tokens ?? 0, cost?.output_tokens ?? 0)}</li>
        {cost?.local_only ? <li>{tr.hud.cost.localOnly}</li> : null}
      </ul>
    </section>
  );
}

const PROVIDER_ICON: Record<HudProviderStatus, typeof CircleCheck> = {
  ok: CircleCheck,
  down: CircleX,
  unknown: CircleDashed,
  disabled: CircleOff,
};

const PROVIDER_TONE: Record<HudProviderStatus, string> = {
  ok: "text-hud-speaking",
  down: "text-hud-error",
  unknown: "text-hud-caption-muted",
  disabled: "text-hud-caption-muted",
};

export type MicState = keyof typeof tr.hud.health.micStates;

export function HealthPanel({
  health,
  mic,
  stream,
}: {
  health: HudHealth | null;
  mic: MicState;
  stream: HudStreamStatus;
}) {
  const providers = health?.providers ?? [];
  const budgets = health?.budgets ?? [];
  const preflight = health?.preflight ?? null;
  return (
    <section className={PANEL} aria-labelledby="hud-health" data-testid="hud-health">
      <h2 id="hud-health" className={HEADING}>
        {tr.hud.health.heading}
      </h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-hud-caption-muted">{tr.hud.health.mic}</dt>
        <dd className="text-hud-caption" data-testid="hud-health-mic" data-mic={mic}>
          {tr.hud.health.micStates[mic]}
        </dd>
        <dt className="text-hud-caption-muted">{tr.hud.health.stream}</dt>
        <dd
          className={cn(stream === "lost" ? "text-hud-disconnected" : "text-hud-caption")}
          data-testid="hud-health-stream"
        >
          {tr.hud.health.streamStates[stream]}
        </dd>
      </dl>

      {providers.length > 0 ? (
        <div className="flex flex-col gap-1">
          <h3 className="text-xs text-hud-caption-muted">{tr.hud.health.providers}</h3>
          <ul className="flex flex-col gap-0.5 text-xs" data-testid="hud-providers">
            {providers.map((provider) => {
              const Icon = PROVIDER_ICON[provider.status];
              return (
                <li
                  key={provider.name}
                  className={cn("flex items-center gap-1.5", PROVIDER_TONE[provider.status])}
                  data-status={provider.status}
                >
                  <Icon aria-hidden className="size-3.5 shrink-0" />
                  <span className="min-w-0 truncate">
                    {provider.name} ({provider.capability},{" "}
                    {provider.local ? tr.hud.health.local : tr.hud.health.cloud})
                  </span>
                  <span className="ml-auto shrink-0">{tr.hud.health.providerStates[provider.status]}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      <div className="flex flex-col gap-1">
        <h3 className="text-xs text-hud-caption-muted">{tr.hud.health.budgets}</h3>
        {budgets.length === 0 ? (
          <p className="text-xs text-hud-caption-muted">{tr.hud.health.noBudgets}</p>
        ) : (
          <ul className="flex flex-col gap-0.5 text-xs" data-testid="hud-budgets">
            {budgets.map((budget) => (
              <li
                key={budget.name}
                className={cn("flex flex-wrap items-center gap-x-2", budget.exhausted ? "text-hud-error" : "text-hud-caption")}
                data-exhausted={budget.exhausted}
              >
                {budget.exhausted ? <AlertTriangle aria-hidden className="size-3.5" /> : null}
                <span>{budget.exhausted ? tr.hud.health.budgetExhausted(budget.name) : budget.name}</span>
                {budget.windows.map((window) => (
                  <span key={window.window} className="text-hud-caption-muted">
                    {tr.hud.health.budgetWindow[window.window] ?? window.window}:{" "}
                    {tr.hud.health.budgetUsage(
                      Number(window.spent_usd).toFixed(2),
                      Number(window.limit_usd).toFixed(2),
                    )}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-col gap-0.5 text-xs" data-testid="hud-preflight">
        <h3 className="text-hud-caption-muted">{tr.hud.health.preflight}</h3>
        {preflight && preflight.status ? (
          <p className={preflight.status === "fail" ? "text-hud-error" : "text-hud-caption"}>
            {tr.hud.health.preflightStates[preflight.status] ?? preflight.status}
            {preflight.at ? ` · ${formatRelativeTime(preflight.at)}` : null}
            {" · "}
            {tr.hud.health.preflightCounts(preflight.pass ?? 0, preflight.fail ?? 0, preflight.warn ?? 0)}
            {preflight.failed_checks && preflight.failed_checks.length > 0
              ? ` (${preflight.failed_checks.join(", ")})`
              : null}
          </p>
        ) : (
          <p className="text-hud-caption-muted">{tr.hud.health.preflightNone}</p>
        )}
      </div>
    </section>
  );
}
