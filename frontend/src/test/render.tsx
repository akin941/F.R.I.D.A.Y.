import { render, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * A fresh QueryClient per test: no cache shared across tests, no retries
 * (a failed request surfaces at once), otherwise the app's defaults
 * (lib/query/client.ts) -- including no polling while the tab is hidden.
 */
export function makeTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, refetchIntervalInBackground: false },
      mutations: { retry: false },
    },
  });
}

export function renderWithClient(
  ui: React.ReactElement,
  { client = makeTestQueryClient(), ...options }: RenderOptions & { client?: QueryClient } = {},
) {
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    );
  }
  return { client, ...render(ui, { wrapper: Wrapper, ...options }) };
}
