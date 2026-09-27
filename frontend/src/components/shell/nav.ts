import {
  Bot,
  Building2,
  CalendarCheck,
  Brain,
  BookOpen,
  ListTodo,
  MessageSquare,
  Plug,
  ShieldCheck,
  Terminal,
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
 * The grouped shell navigation (Phase 4 branch 3). Voice ("Sesli") is off
 * the nav until Phase 6 -- its route stays. "Cihazlar" (Ayarlar) arrives
 * with the device-trust branch, which owns the /devices route.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    id: "work",
    label: groups.work,
    items: [
      { href: "/chat", label: items.chat, icon: MessageSquare },
      { href: "/approvals", label: items.approvals, icon: ShieldCheck, badge: "approvals" },
      { href: "/harness", label: items.harness, icon: Terminal, badge: "harness" },
    ],
  },
  {
    id: "memory",
    label: groups.memory,
    items: [
      { href: "/memory", label: items.memory, icon: Brain },
      { href: "/knowledge", label: items.knowledge, icon: BookOpen },
      { href: "/tasks", label: items.tasks, icon: ListTodo },
      { href: "/today", label: items.today, icon: CalendarCheck },
    ],
  },
  {
    id: "firm",
    label: groups.firm,
    items: [{ href: "/workspaces", label: items.workspaces, icon: Building2 }],
  },
  {
    id: "automation",
    label: groups.automation,
    items: [{ href: "/agents", label: items.agents, icon: Bot, badge: "agents" }],
  },
  {
    id: "settings",
    label: groups.settings,
    items: [{ href: "/integrations", label: items.integrations, icon: Plug }],
  },
];

/** Whether `href` is the current section (a detail route counts too). */
export function isActivePath(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}
