import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { DeepLinkTarget, useDeepLinkTarget } from "@/components/shell/deep-link-target";

function List({ ready, ids }: { ready: boolean; ids: string[] }) {
  const target = useDeepLinkTarget("memory", ready);
  return (
    <div>
      {ids.map((id) => (
        <DeepLinkTarget key={id} kind="memory" id={id} active={target === id}>
          <p>{id}</p>
        </DeepLinkTarget>
      ))}
    </div>
  );
}

describe("useDeepLinkTarget", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
    vi.restoreAllMocks();
  });

  it("highlights and scrolls to the linked item once the list is ready", () => {
    window.history.replaceState(null, "", "/memory?workspace=w1&memory=m2");
    const scrolled = vi.fn();
    vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(scrolled);

    const { rerender } = render(<List ready={false} ids={[]} />);
    expect(scrolled).not.toHaveBeenCalled();

    rerender(<List ready ids={["m1", "m2"]} />);
    const item = screen.getByText("m2").parentElement!;
    expect(item).toHaveAttribute("id", "memory-m2");
    expect(item).toHaveAttribute("data-deep-link-active", "true");
    expect(screen.getByText("m1").parentElement).not.toHaveAttribute("data-deep-link-active");
    expect(scrolled).toHaveBeenCalledTimes(1);

    rerender(<List ready ids={["m1", "m2", "m3"]} />);
    expect(scrolled).toHaveBeenCalledTimes(1);
  });

  it("does nothing without the parameter", () => {
    window.history.replaceState(null, "", "/memory");
    render(<List ready ids={["m1"]} />);
    expect(screen.getByText("m1").parentElement).not.toHaveAttribute("data-deep-link-active");
  });
});
