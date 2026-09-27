import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { clickableProps } from "@/lib/keyboard";

describe("clickableProps", () => {
  it("exposes a focusable button role and activates on click, Enter, and Space", () => {
    const onActivate = vi.fn();
    render(<div {...clickableProps(onActivate)}>Row</div>);

    const row = screen.getByRole("button", { name: "Row" });
    expect(row).toHaveAttribute("tabindex", "0");

    fireEvent.click(row);
    fireEvent.keyDown(row, { key: "Enter" });
    fireEvent.keyDown(row, { key: " " });
    fireEvent.keyDown(row, { key: "a" });

    expect(onActivate).toHaveBeenCalledTimes(3);
  });

  it("ignores key presses bubbling from a nested control", () => {
    const onActivate = vi.fn();
    render(
      <div {...clickableProps(onActivate)}>
        <input aria-label="nested" />
      </div>,
    );

    fireEvent.keyDown(screen.getByLabelText("nested"), { key: "Enter" });

    expect(onActivate).not.toHaveBeenCalled();
  });
});
