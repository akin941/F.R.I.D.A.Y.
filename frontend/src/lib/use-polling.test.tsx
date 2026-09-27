import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { usePolling } from "@/lib/use-polling";

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("usePolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setHidden(false);
  });

  afterEach(() => {
    setHidden(false);
    vi.useRealTimers();
  });

  it("calls back on the given cadence and not at all when the interval is null", () => {
    const callback = vi.fn();
    const { rerender } = renderHook(({ ms }) => usePolling(callback, ms), {
      initialProps: { ms: 1000 as number | null },
    });

    act(() => vi.advanceTimersByTime(3000));
    expect(callback).toHaveBeenCalledTimes(3);

    rerender({ ms: null });
    act(() => vi.advanceTimersByTime(5000));
    expect(callback).toHaveBeenCalledTimes(3);
  });

  it("does not restart the interval on an unrelated re-render", () => {
    const callback = vi.fn();
    const { rerender } = renderHook(({ ms }) => usePolling(callback, ms), {
      initialProps: { ms: 1000 },
    });

    act(() => vi.advanceTimersByTime(600));
    rerender({ ms: 1000 });
    act(() => vi.advanceTimersByTime(400));

    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("pauses while the tab is hidden and refreshes once on becoming visible", () => {
    const callback = vi.fn();
    renderHook(() => usePolling(callback, 1000));

    act(() => setHidden(true));
    act(() => vi.advanceTimersByTime(10_000));
    expect(callback).not.toHaveBeenCalled();

    act(() => setHidden(false));
    expect(callback).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(1000));
    expect(callback).toHaveBeenCalledTimes(2);
  });
});
