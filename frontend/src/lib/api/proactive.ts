"use client";

import { apiDelete, apiGetJson, apiPostJson, apiPutJson } from "@/lib/api-client";

/**
 * The proactive surfaces (Phase 12 D3/D5/D6/D20) over the same-origin BFF
 * routes (`/api/proactive/*` -> backend `/api/v1/proactive/*`). Owner, web
 * session only on the backend; nothing here decides anything -- a notice or
 * a digest only links to the page where the owner acts.
 */

export interface DigestItem {
  label: string;
  link: string;
}

export interface DigestEntry {
  probe: string;
  name: string;
  count: number;
  link: string;
  items: DigestItem[];
}

export interface DigestSection {
  section: string;
  title: string;
  entries: DigestEntry[];
}

export interface Digest {
  id: string;
  workspace: string;
  created_at: string | null;
  spoken_text: string;
  sections: DigestSection[];
  composed_by: "model" | "template";
  data_class: "personal" | "firm";
  opened_at: string | null;
  spoken_at: string | null;
  spoken_channel: "" | "web" | "node_voice";
  speak_on_open: boolean;
}

export type ScheduleKind = "morning_digest" | "heartbeat";

export interface Schedule {
  id: string;
  kind: ScheduleKind;
  enabled: boolean;
  local_time: string | null;
  weekdays: number;
  speak_on_open: boolean;
  interval_minutes: number | null;
  quiet_start: string | null;
  quiet_end: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
}

export interface ScheduleList {
  workspace: string;
  paused: boolean;
  schedules: Schedule[];
}

export interface ScheduleWrite {
  enabled: boolean;
  local_time?: string | null;
  weekdays?: number | null;
  speak_on_open?: boolean | null;
  interval_minutes?: number | null;
  quiet_start?: string | null;
  quiet_end?: string | null;
}

export interface Notice {
  id: string;
  kind: string;
  title: string;
  count: number;
  link: string;
  data_class: string;
  created_at: string;
  read_at: string | null;
  dismissed_at: string | null;
}

export interface NoticeList {
  workspace: string;
  unread: number;
  notices: Notice[];
}

const ws = (workspaceId: string) => `workspace=${encodeURIComponent(workspaceId)}`;

export async function fetchLatestDigest(workspaceId: string): Promise<Digest | null> {
  const body = await apiGetJson<{ digest: Digest | null }>(`/api/proactive/digests/latest?${ws(workspaceId)}`);
  return body.digest ?? null;
}

export function ackDigest(digestId: string, event: "opened" | "spoken"): Promise<Digest & { changed: boolean }> {
  return apiPostJson(`/api/proactive/digests/${encodeURIComponent(digestId)}/ack`, { event });
}

export function fetchSchedules(workspaceId: string): Promise<ScheduleList> {
  return apiGetJson(`/api/proactive/schedules?${ws(workspaceId)}`);
}

export function saveSchedule(workspaceId: string, kind: ScheduleKind, body: ScheduleWrite): Promise<Schedule> {
  return apiPutJson(`/api/proactive/schedules/${kind}?${ws(workspaceId)}`, body);
}

export async function fetchPause(workspaceId: string): Promise<boolean> {
  const body = await apiGetJson<{ paused: boolean }>(`/api/proactive/pause?${ws(workspaceId)}`);
  return Boolean(body.paused);
}

export async function setPause(workspaceId: string, paused: boolean): Promise<boolean> {
  if (paused) {
    const body = await apiPostJson<{ paused: boolean }>(`/api/proactive/pause?${ws(workspaceId)}`);
    return Boolean(body.paused);
  }
  await apiDelete(`/api/proactive/pause?${ws(workspaceId)}`);
  return false;
}

export function fetchNotices(workspaceId: string, state: "unread" | "all" = "unread"): Promise<NoticeList> {
  return apiGetJson(`/api/proactive/notices?${ws(workspaceId)}&state=${state}`);
}

export function markNoticeRead(noticeId: string): Promise<Notice> {
  return apiPostJson(`/api/proactive/notices/${encodeURIComponent(noticeId)}/read`);
}

export function dismissAllNotices(workspaceId: string): Promise<{ dismissed: number }> {
  return apiPostJson(`/api/proactive/notices/dismiss-all?${ws(workspaceId)}`);
}

/** Weekday mask bits (bit 0 = Monday ... bit 6 = Sunday), as the backend. */
export const WEEKDAY_LABELS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"] as const;

export function weekdaysFromMask(mask: number): boolean[] {
  return WEEKDAY_LABELS.map((_, index) => (mask & (1 << index)) !== 0);
}

export function maskFromWeekdays(days: boolean[]): number {
  return days.reduce((mask, on, index) => (on ? mask | (1 << index) : mask), 0);
}

/** Only links from the backend's closed set (core.probes) are followed. */
const LINK_PATTERNS = [
  /^\/approvals$/,
  /^\/approvals\/[A-Za-z0-9-]{1,64}$/,
  /^\/tasks$/,
  /^\/today$/,
  /^\/memory\/learning$/,
  /^\/sessions$/,
  /^\/harness$/,
  /^\/harness\/[A-Za-z0-9-]{1,64}$/,
  /^\/harness\/implementations\/[A-Za-z0-9-]{1,64}$/,
  /^\/devices$/,
  /^\/chat\/[A-Za-z0-9-]{1,64}$/,
  /^\/skills$/,
  /^\/skills\/[A-Za-z0-9-]{1,64}$/,
];

export function safeLink(link: string | null | undefined): string | null {
  if (!link) return null;
  return LINK_PATTERNS.some((pattern) => pattern.test(link)) ? link : null;
}
