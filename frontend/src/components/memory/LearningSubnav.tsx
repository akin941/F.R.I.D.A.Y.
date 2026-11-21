"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";

const copy = tr.learning.subnav;

const LINKS = [
  { href: "/memory/learning", label: copy.learning },
  { href: "/memory/ingestion", label: copy.ingestion },
  { href: "/memory/core-blocks", label: copy.coreBlocks },
  { href: "/memory/graph", label: copy.graph },
  { href: "/memory/evals", label: copy.evals },
];

/** The learning-memory pages' shared sub-navigation (wraps at phone width). */
export function LearningSubnav() {
  const pathname = usePathname() ?? "";
  return (
    <nav aria-label={copy.label} className="flex flex-wrap gap-1.5" data-testid="learning-subnav">
      {LINKS.map((link) => {
        const active = pathname === link.href;
        return (
          <Link
            key={link.href}
            href={link.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "rounded-md border px-3 py-1.5 text-sm font-medium outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50",
              active && "bg-accent text-accent-foreground",
            )}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
