import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { JarvisOrb } from "@/components/voice/JarvisOrb";

describe("JarvisOrb", () => {
  it.each([
    ["idle", "Jarvis boşta"],
    ["thinking", "Jarvis düşünüyor"],
    ["speaking", "Jarvis konuşuyor"],
  ] as const)("labels the %s state for assistive tech", (state, label) => {
    render(<JarvisOrb state={state} />);
    expect(screen.getByRole("img", { name: label })).toBeInTheDocument();
  });

  it("renders a canvas sized to the given prop", () => {
    render(<JarvisOrb state="idle" size={120} />);
    const canvas = screen.getByRole("img", { name: "Jarvis boşta" });
    expect(canvas.tagName).toBe("CANVAS");
    expect(canvas).toHaveStyle({ width: "120px", height: "120px" });
  });
});
