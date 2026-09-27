import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { fastestPollInterval, pollWhile } from "@/lib/query/polling";
import { makeTestQueryClient } from "@/test/render";
import { setDocumentHidden } from "@/test/visibility";

// Ported from the retired `usePolling` hook's tests: the same cadence,
// stop and visibility guarantees, now as TanStack Query options.

type Row = { status: "running" | "done" };

function usePolled(fetcher: () => Promise<Row[]>, cadenceMs = 1000) {
  return useQuery({
    queryKey: ["w1", "rows"],
    queryFn: fetcher,
    refetchInterval: pollWhile<Row[]>((rows) =>
      rows.some((r) => r.status === "running") ? cadenceMs : null,
    ),
  });
}

function setup(fetcher: () => Promise<Row[]>) {
  const client = makeTestQueryClient();
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => usePolled(fetcher), { wrapper });
}

describe("pollWhile", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setDocumentHidden(false);
  });

  afterEach(() => {
    setDocumentHidden(false);
    vi.useRealTimers();
  });

  it("polls on the cadence while anything is in flight", async () => {
    const fetcher = vi.fn(async (): Promise<Row[]> => [{ status: "running" }]);
    setup(fetcher);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it("stops polling once every row is terminal", async () => {
    let calls = 0;
    const fetcher = vi.fn(async (): Promise<Row[]> => {
      calls += 1;
      return [{ status: calls < 3 ? "running" : "done" }];
    });
    setup(fetcher);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(fetcher).toHaveBeenCalledTimes(3);
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("never polls when nothing is in flight", async () => {
    const fetcher = vi.fn(async (): Promise<Row[]> => [{ status: "done" }]);
    setup(fetcher);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("pauses while the tab is hidden and refreshes once on becoming visible", async () => {
    const fetcher = vi.fn(async (): Promise<Row[]> => [{ status: "running" }]);
    setup(fetcher);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    act(() => setDocumentHidden(true));
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(fetcher).toHaveBeenCalledTimes(1);

    act(() => setDocumentHidden(false));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
});

describe("fastestPollInterval", () => {
  it("picks the fastest active cadence, or null when all are idle", () => {
    expect(fastestPollInterval([null, 30_000, 3_000])).toBe(3_000);
    expect(fastestPollInterval([null, null])).toBeNull();
    expect(fastestPollInterval([])).toBeNull();
  });
});
