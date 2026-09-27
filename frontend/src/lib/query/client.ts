import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api-client";

/**
 * One QueryClient per browser tab (created in `app/providers.tsx`).
 *
 * - `refetchIntervalInBackground: false` + `refetchOnWindowFocus: true`
 *   is `usePolling`'s visibility behavior: a poll never fires while the
 *   tab is hidden, and becoming visible again refreshes once immediately.
 * - `staleTime: 0`: every mount revalidates (stale-while-revalidate --
 *   cached rows render at once, then refresh), same as the old
 *   fetch-on-mount effects.
 * - A 4xx is an answer, not a transient failure: never retried. Network
 *   errors and 5xx get one retry.
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 0,
        refetchOnWindowFocus: true,
        refetchIntervalInBackground: false,
        retry: (failureCount, error) =>
          !(error instanceof ApiError && error.status < 500) && failureCount < 1,
      },
      mutations: { retry: false },
    },
  });
}
