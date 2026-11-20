import {
  Bot,
  Building2,
  Cable,
  CalendarCheck,
  Gauge,
  Brain,
  BookOpen,
  ListTodo,
  MonitorSmartphone,
  MessageSquare,
  Mic,
  Plug,
  Rocket,
  ShieldCheck,
  Sunrise,
  Terminal,
  TerminalSquare,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { tr } from "@/lib/i18n/tr";

export type NavBadge = "approvals" | "harness" | "agents";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  badge?: NavBadge;
}

export interface NavGroup {
  id: keyof typeof tr.nav.groups;
  label: string;
  items: NavItem[];
}

const { items, groups } = tr.nav;

/**
 * The grouped shell navigation (Phase 4 branch 3). Voice ("Sesli") is back
 * in the nav with Phase 6 push-to-talk (branch 3). "Cihazlar" (Ayarlar) is the
 * device-trust branch's /devices route.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    id: "work",
    label: groups.work,
    items: [
      { href: "/chat", label: items.chat, icon: MessageSquare },
      { href: "/voice", label: items.voice, icon: Mic },
      { href: "/hud", label: items.hud, icon: Gauge },
      { href: "/approvals", label: items.approvals, icon: ShieldCheck, badge: "approvals" },
      { href: "/harness", label: items.harness, icon: Terminal, badge: "harness" },
      { href: "/sessions", label: items.sessions, icon: TerminalSquare },
    ],
  },
  {
    id: "memory",
    label: groups.memory,
    items: [
      { href: "/memory", label: items.memory, icon: Brain },
      { href: "/memory/learning", label: items.learning, icon: Sunrise },
      { href: "/knowledge", label: items.knowledge, icon: BookOpen },
      { href: "/tasks", label: items.tasks, icon: ListTodo },
      { href: "/today", label: items.today, icon: CalendarCheck },
      { href: "/trash", label: items.trash, icon: Trash2 },
    ],
  },
  {
    id: "firm",
    label: groups.firm,
    items: [
      { href: "/workspaces", label: items.workspaces, icon: Building2 },
      { href: "/deploys", label: items.deploys, icon: Rocket },
    ],
  },
  {
    id: "automation",
    label: groups.automation,
    items: [{ href: "/agents", label: items.agents, icon: Bot, badge: "agents" }],
  },
  {
    id: "settings",
    label: groups.settings,
    items: [
      { href: "/devices", label: items.devices, icon: MonitorSmartphone },
      { href: "/integrations", label: items.integrations, icon: Plug },
      { href: "/connectors", label: items.connectors, icon: Cable },
    ],
  },
];

/** Whether `href` is the current section (a detail route counts too). */
export function isActivePath(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The one nav href that marks `pathname` current: the longest matching
 * one, so `/memory/learning` (and its sibling learning pages) does not
 * also light up `/memory`.
 */
export function activeNavHref(pathname: string, groups: NavGroup[] = NAV_GROUPS): string | null {
  let best: string | null = null;
  for (const group of groups) {
    for (const item of group.items) {
      if (isActivePath(pathname, item.href) && (best === null || item.href.length > best.length)) {
        best = item.href;
      }
    }
  }
  return best;
}
