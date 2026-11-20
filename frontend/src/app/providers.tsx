"use client";

import { useState } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { makeQueryClient } from "@/lib/query/client";

/**
 * App-wide client providers:
 * - next-themes with the class strategy (shadcn convention): its inline
 *   script sets `class="dark"` on <html> before first paint, so there is
 *   no light/dark flash; "system" follows prefers-color-scheme.
 * - one QueryClient per tab (lazy state, never recreated on re-render).
 * - the Tooltip delay group and the toast outlet.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(makeQueryClient);
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      storageKey="friday:theme"
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
