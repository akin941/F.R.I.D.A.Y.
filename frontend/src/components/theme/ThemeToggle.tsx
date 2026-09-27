"use client";

import { useSyncExternalStore } from "react";
import { useTheme } from "next-themes";
import { Monitor, Moon, Sun, type LucideIcon } from "lucide-react";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";

const OPTIONS: { value: "light" | "dark" | "system"; label: string; icon: LucideIcon }[] = [
  { value: "light", label: tr.theme.light, icon: Sun },
  { value: "dark", label: tr.theme.dark, icon: Moon },
  { value: "system", label: tr.theme.system, icon: Monitor },
];

const noopSubscribe = () => () => {};

/**
 * Light / dark / system switch (a radio group). The stored choice is only
 * known on the client, so nothing is marked checked until after hydration
 * -- the server markup and the first client render agree.
 */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const hydrated = useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );
  const current = hydrated ? (theme ?? "system") : null;

  // Radio-group keyboard contract: one tab stop, arrows move the choice.
  function onKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const next = OPTIONS[(index + step + OPTIONS.length) % OPTIONS.length]!;
    setTheme(next.value);
    const group = event.currentTarget.parentElement;
    group?.querySelector<HTMLButtonElement>(`[data-value="${next.value}"]`)?.focus();
  }

  return (
    <div
      role="radiogroup"
      aria-label={tr.theme.label}
      className={cn("inline-flex items-center gap-0.5 rounded-lg border p-0.5", className)}
    >
      {OPTIONS.map(({ value, label, icon: Icon }, index) => {
        const checked = current === value;
        const focusable = checked || (current === null && value === "system");
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={label}
            data-value={value}
            tabIndex={focusable ? 0 : -1}
            onKeyDown={(event) => onKeyDown(event, index)}
            title={label}
            onClick={() => setTheme(value)}
            className={cn(
              "inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
              checked && "bg-accent text-accent-foreground",
            )}
          >
            <Icon aria-hidden className="size-4" />
          </button>
        );
      })}
    </div>
  );
}
