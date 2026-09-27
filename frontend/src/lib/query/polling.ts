/**
 * Conditional polling as TanStack Query options (replaces the old
 * `usePolling` hook and the hand-rolled `setInterval` pollers).
 */

// Something is actively changing on its own (a worker/container running).
export const POLL_FAST_MS = 3_000;
// Only a human can move it forward (e.g. a gate awaiting the owner's
// decision) -- refresh occasionally in case it was decided elsewhere.
export const POLL_SLOW_MS = 30_000;

/**
 * `refetchInterval` for a query whose cadence depends on its own data:
 * `intervalFor(data)` ms, or no polling at all once it returns null (every
 * row terminal). Nothing polls before the first successful load. The
 * interval only restarts when the cadence changes, and -- with the client
 * defaults -- never fires while the tab is hidden.
 */
export function pollWhile<T>(intervalFor: (data: T) => number | null) {
  return (query: { state: { data: T | undefined } }): number | false => {
    const data = query.state.data;
    if (data === undefined) return false;
    return intervalFor(data) ?? false;
  };
}

/** The fastest cadence any of `intervals` needs (null when all are null). */
export function fastestPollInterval(intervals: (number | null)[]): number | null {
  const active = intervals.filter((ms): ms is number => ms != null);
  return active.length > 0 ? Math.min(...active) : null;
}
