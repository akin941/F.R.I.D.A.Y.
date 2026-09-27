"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApprovalCard, INBOX_ITEM_ATTRIBUTE } from "@/components/approvals/ApprovalCard";
import {
  APPROVAL_STATUSES,
  APPROVAL_STATUS_LABELS,
  canDecideInRole,
} from "@/components/approvals/labels";
import { listPollInterval } from "@/components/approvals/polling";
import { RENDERED_TOOL_NAMES } from "@/components/approvals/renderers";
import { toolTitle } from "@/components/approvals/renderers/shared";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import { pollWhile } from "@/lib/query/polling";
import type {
  ApiApprovalRequest,
  ApprovalOrigin,
  ApprovalRequestedChannel,
} from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

const copy = tr.approvals.inbox;
const ALL = "all";
const ORIGINS: ApprovalOrigin[] = ["human", "agent", "harness"];
const CHANNELS: ApprovalRequestedChannel[] = ["web", "cli", "telegram", "system", "unknown"];

/** The inbox filters, each `"all"` or one exact value (server-side filters). */
export interface InboxFilters {
  status: string;
  tool: string;
  workspace: string;
  origin: string;
  requested_channel: string;
}

const NO_FILTERS: InboxFilters = {
  status: ALL,
  tool: ALL,
  workspace: ALL,
  origin: ALL,
  requested_channel: ALL,
};

/** Only the set filters, for the query key and the query string. */
function activeFilters(filters: InboxFilters): Record<string, string> {
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== ALL));
}

export function inboxUrl(filters: InboxFilters): string {
  const query = new URLSearchParams({ scope: "all", ...activeFilters(filters) });
  return `/api/approvals/requests?${query.toString()}`;
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
    target.getAttribute("role") === "combobox"
  );
}

/**
 * j / k move focus between the inbox's cards (their title links); Enter
 * on a focused link opens its detail as any link does. Navigation only --
 * there is deliberately no single-key approve or reject: a decision always
 * goes through its own dialog. Ignored while typing, with a modifier, or
 * while a dialog is open.
 */
function useInboxKeyboardNavigation(container: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "j" && event.key !== "k") return;
      if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      if (isTypingTarget(event.target)) return;
      if (document.querySelector('[role="alertdialog"], [role="dialog"]')) return;
      const root = container.current;
      if (!root) return;
      const items = Array.from(root.querySelectorAll<HTMLElement>(`[${INBOX_ITEM_ATTRIBUTE}]`));
      if (items.length === 0) return;
      event.preventDefault();
      const current = items.indexOf(document.activeElement as HTMLElement);
      const next =
        current === -1
          ? event.key === "j"
            ? 0
            : items.length - 1
          : Math.min(items.length - 1, Math.max(0, current + (event.key === "j" ? 1 : -1)));
      const target = items[next];
      target?.focus();
      if (target && typeof target.scrollIntoView === "function") {
        target.scrollIntoView({ block: "nearest" });
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [container]);
}

function FilterSelect({
  label,
  value,
  onChange,
  allLabel,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  allLabel: string;
  options: { value: string; label: string }[];
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger aria-label={label} className="min-w-40">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{allLabel}</SelectItem>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * `/approvals` (Phase 4 branch 5): one inbox across every firm the owner
 * belongs to (`scope=all`; the backend's `for_actor()` limits rows to
 * memberships), each row labeled with its workspace. Filters by status,
 * tool, workspace, origin and requested channel (server-side). Pending
 * requests first, then the decided history. Each card links to its routed
 * detail (`/approvals/[requestId]`).
 */
export function ApprovalInbox() {
  const queryClient = useQueryClient();
  const { workspaces } = useWorkspace();
  const [filters, setFilters] = useState<InboxFilters>(NO_FILTERS);
  const listRef = useRef<HTMLDivElement>(null);
  useInboxKeyboardNavigation(listRef);

  // Polls while something can still change on its own -- an in-flight
  // execution (fast/slow per tool) or a pending request (slow, for a
  // decision made elsewhere or the TTL) -- never while the tab is hidden.
  const query = useQuery({
    queryKey: queryKeys.approvalInbox(activeFilters(filters)),
    queryFn: () => apiGetJson<ApiApprovalRequest[]>(inboxUrl(filters)),
    refetchInterval: pollWhile<ApiApprovalRequest[]>(listPollInterval),
  });
  const requests = useMemo(() => query.data ?? [], [query.data]);

  const workspaceById = useMemo(
    () => new Map((workspaces ?? []).map((workspace) => [workspace.id, workspace])),
    [workspaces],
  );

  function handleChanged(updated: ApiApprovalRequest) {
    queryClient.setQueryData<ApiApprovalRequest[]>(
      queryKeys.approvalInbox(activeFilters(filters)),
      (prev = []) => prev.map((r) => (r.id === updated.id ? updated : r)),
    );
    // Other filter views, the detail page and the nav badge.
    void queryClient.invalidateQueries({ queryKey: queryKeys.allApprovals() });
    void queryClient.invalidateQueries({ queryKey: queryKeys.activitySummary() });
  }

  function setFilter(key: keyof InboxFilters, value: string) {
    setFilters((prev) => ({ ...prev, [key]: value }));
  }

  const pending = requests.filter((r) => r.status === "pending");
  const decided = requests.filter((r) => r.status !== "pending");
  const filtered = Object.values(filters).some((value) => value !== ALL);
  const showPending = filters.status === ALL || filters.status === "pending";
  const showHistory = filters.status !== "pending";

  function card(request: ApiApprovalRequest) {
    const workspace = workspaceById.get(request.workspace);
    return (
      <ApprovalCard
        key={request.id}
        request={request}
        workspaceName={workspace?.name ?? copy.unknownWorkspace}
        canDecide={canDecideInRole(workspace?.role)}
        onChanged={handleChanged}
      />
    );
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <PageHeader title={copy.title} description={copy.description} />

      <div role="group" aria-label={copy.filters} className="flex flex-wrap items-center gap-2">
        <FilterSelect
          label={copy.statusFilter}
          value={filters.status}
          onChange={(value) => setFilter("status", value)}
          allLabel={copy.allStatuses}
          options={APPROVAL_STATUSES.map((status) => ({
            value: status,
            label: APPROVAL_STATUS_LABELS[status],
          }))}
        />
        <FilterSelect
          label={copy.toolFilter}
          value={filters.tool}
          onChange={(value) => setFilter("tool", value)}
          allLabel={copy.allTools}
          options={RENDERED_TOOL_NAMES.map((tool) => ({ value: tool, label: toolTitle(tool) }))}
        />
        {(workspaces ?? []).length > 1 ? (
          <FilterSelect
            label={copy.workspaceFilter}
            value={filters.workspace}
            onChange={(value) => setFilter("workspace", value)}
            allLabel={copy.allWorkspaces}
            options={(workspaces ?? []).map((workspace) => ({
              value: workspace.id,
              label: workspace.name,
            }))}
          />
        ) : null}
        <FilterSelect
          label={copy.originFilter}
          value={filters.origin}
          onChange={(value) => setFilter("origin", value)}
          allLabel={copy.allOrigins}
          options={ORIGINS.map((origin) => ({ value: origin, label: tr.approvals.origin[origin]! }))}
        />
        <FilterSelect
          label={copy.channelFilter}
          value={filters.requested_channel}
          onChange={(value) => setFilter("requested_channel", value)}
          allLabel={copy.allChannels}
          options={CHANNELS.map((channel) => ({
            value: channel,
            label: tr.approvals.channel[channel]!,
          }))}
        />
        {filtered ? (
          <Button variant="ghost" size="sm" onClick={() => setFilters(NO_FILTERS)}>
            {copy.clearFilters}
          </Button>
        ) : null}
      </div>
      <p className="text-xs text-muted-foreground">{copy.shortcuts}</p>

      {query.isError && !query.data ? (
        <ErrorState
          error={query.error}
          title={copy.loadFailed}
          onRetry={() => void query.refetch()}
          retrying={query.isFetching}
        />
      ) : query.isPending ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <div ref={listRef} className="flex flex-col gap-6">
          {showPending ? (
            <section aria-labelledby="approvals-pending" className="flex flex-col gap-3">
              <h2 id="approvals-pending" className="text-lg font-medium tracking-tight">
                {copy.pending} {pending.length > 0 ? `(${pending.length})` : ""}
              </h2>
              {pending.length === 0 ? (
                <p className="text-sm text-muted-foreground">{copy.noPending}</p>
              ) : (
                <div className="flex flex-col gap-3">{pending.map(card)}</div>
              )}
            </section>
          ) : null}

          {showPending && showHistory ? <Separator /> : null}

          {showHistory ? (
            <section aria-labelledby="approvals-history" className="flex flex-col gap-3">
              <h2 id="approvals-history" className="text-lg font-medium tracking-tight">
                {copy.history}
              </h2>
              {decided.length === 0 ? (
                <p className="text-sm text-muted-foreground">{copy.noHistory}</p>
              ) : (
                <div className="flex flex-col gap-3">{decided.map(card)}</div>
              )}
            </section>
          ) : null}
        </div>
      )}
    </div>
  );
}
