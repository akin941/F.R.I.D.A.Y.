"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { WorkspaceSwitcher } from "@/components/shell/WorkspaceSwitcher";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiPostJson } from "@/lib/api-client";

const NAV_ITEMS = [
  { href: "/chat", label: "Chat" },
  { href: "/memory", label: "Memory" },
  { href: "/knowledge", label: "Knowledge" },
  { href: "/tasks", label: "Tasks" },
  { href: "/today", label: "Today" },
  { href: "/approvals", label: "Approvals" },
  { href: "/agents", label: "Agents" },
];

export function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading } = useWorkspace();

  async function handleLogout() {
    await apiPostJson("/api/auth/logout").catch(() => undefined);
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="flex h-full flex-col gap-4 p-4">
      <div className="text-lg font-semibold tracking-tight">Jarvis</div>
      <WorkspaceSwitcher />
      <Separator />
      <nav className="flex flex-col gap-1" aria-label="Primary">
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            className={cn(
              "rounded-md px-3 py-2 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground",
              pathname.startsWith(item.href) && "bg-accent text-accent-foreground",
            )}
          >
            {item.label}
          </Link>
        ))}
      </nav>
      <div className="mt-auto flex flex-col gap-2">
        <Separator />
        {loading ? (
          <Skeleton className="h-9 w-full" />
        ) : (
          <p className="truncate text-sm text-muted-foreground" title={user?.email}>
            {user?.name || user?.email}
          </p>
        )}
        <Button variant="outline" onClick={handleLogout}>
          Log out
        </Button>
      </div>
    </div>
  );
}
