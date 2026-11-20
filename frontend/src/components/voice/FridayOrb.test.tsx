import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { FridayOrb } from "@/components/voice/FridayOrb";

describe("FridayOrb", () => {
  it.each([
    ["idle", "Friday boşta"],
    ["armed", "Friday dinlemeye hazır"],
    ["listening", "Friday dinliyor"],
    ["transcribing", "Friday yazıya döküyor"],
    ["thinking", "Friday düşünüyor"],
    ["speaking", "Friday konuşuyor"],
    ["error", "Friday: bir sorun var"],
  ] as const)("labels the %s state for assistive tech", (state, label) => {
    render(<FridayOrb state={state} level={() => 0.5} />);
    const orb = screen.getByRole("img", { name: label });
    expect(orb).toHaveAttribute("data-state", state);
  });

  it("renders a canvas sized to the given prop", () => {
    render(<FridayOrb state="idle" size={120} />);
    const canvas = screen.getByRole("img", { name: "Friday boşta" });
    expect(canvas.tagName).toBe("CANVAS");
    expect(canvas).toHaveStyle({ width: "120px", height: "120px" });
  });
});
