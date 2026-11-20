import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { FridayOrb } from "@/components/voice/FridayOrb";

describe("FridayOrb", () => {
  it.each([
    ["idle", "Friday boşta"],
    ["thinking", "Friday düşünüyor"],
    ["speaking", "Friday konuşuyor"],
  ] as const)("labels the %s state for assistive tech", (state, label) => {
    render(<FridayOrb state={state} />);
    expect(screen.getByRole("img", { name: label })).toBeInTheDocument();
  });

  it("renders a canvas sized to the given prop", () => {
    render(<FridayOrb state="idle" size={120} />);
    const canvas = screen.getByRole("img", { name: "Friday boşta" });
    expect(canvas.tagName).toBe("CANVAS");
    expect(canvas).toHaveStyle({ width: "120px", height: "120px" });
  });
});
