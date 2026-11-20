"use client";

import { useState } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { migrateLegacyStorageKey } from "@/lib/legacy-storage";
import { makeQueryClient } from "@/lib/query/client";

const THEME_STORAGE_KEY = "friday:theme";
const LEGACY_THEME_STORAGE_KEY = "jarvis:theme";

/**
 * App-wide client providers:
 * - next-themes with the class strategy (shadcn convention): its inline
 *   script sets `class="dark"` on <html> before first paint, so there is
 *   no light/dark flash; "system" follows prefers-color-scheme.
 * - one QueryClient per tab (lazy state, never recreated on re-render).
 * - the Tooltip delay group and the toast outlet.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  // Before next-themes' first client read of the key (its state
  // initializer), so a pre-rename theme choice carries over. On the very
  // first load after the upgrade the pre-paint script still saw no key.
  useState(() => migrateLegacyStorageKey(THEME_STORAGE_KEY, LEGACY_THEME_STORAGE_KEY));
  const [queryClient] = useState(makeQueryClient);
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      storageKey={THEME_STORAGE_KEY}
    >
      <QueryClientProvider client={queryClient}>
        <TooltipProvider delayDuration={300}>
          {children}
          <Toaster />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
