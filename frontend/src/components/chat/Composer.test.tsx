import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Composer, shouldSendOnKeyDown } from "@/components/chat/Composer";

describe("shouldSendOnKeyDown", () => {
  const key = (overrides: Partial<Parameters<typeof shouldSendOnKeyDown>[0]> = {}) => ({
    key: "Enter",
    shiftKey: false,
    nativeEvent: { isComposing: false },
    ...overrides,
  });

  it("sends on Enter, not on Shift+Enter or other keys", () => {
    expect(shouldSendOnKeyDown(key())).toBe(true);
    expect(shouldSendOnKeyDown(key({ shiftKey: true }))).toBe(false);
    expect(shouldSendOnKeyDown(key({ key: "a" }))).toBe(false);
  });

  it("never sends while an IME composition is being confirmed", () => {
    expect(shouldSendOnKeyDown(key({ nativeEvent: { isComposing: true } }))).toBe(false);
    // Legacy browsers report the composition Enter as keyCode 229 instead.
    expect(shouldSendOnKeyDown(key({ keyCode: 229 }))).toBe(false);
  });
});

function renderComposer(props: Partial<React.ComponentProps<typeof Composer>> = {}) {
  const onSend = vi.fn();
  render(
    <Composer
      value="merhaba"
      onChange={() => {}}
      onSend={onSend}
      onStop={() => {}}
      isStreaming={false}
      disabled={false}
      {...props}
    />,
  );
  return { onSend, textarea: screen.getByRole("textbox", { name: "Mesaj" }) };
}

describe("Composer", () => {
  it("Enter sends; Shift+Enter and a composing Enter do not", () => {
    const { onSend, textarea } = renderComposer();
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(textarea, { key: "Enter", isComposing: true });
    fireEvent.keyDown(textarea, { key: "Enter", keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("does not send blank text", () => {
    const blank = renderComposer({ value: "   " });
    fireEvent.keyDown(blank.textarea, { key: "Enter" });
    expect(blank.onSend).not.toHaveBeenCalled();
  });

  it("stays editable while streaming but offers Stop instead of Send", () => {
    const { onSend, textarea } = renderComposer({ isStreaming: true });
    expect(textarea).toBeEnabled();
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Durdur" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Gönder" })).not.toBeInTheDocument();
  });

  it("grows with its content up to a cap", () => {
    const props = {
      onChange: () => {},
      onSend: () => {},
      onStop: () => {},
      isStreaming: false,
      disabled: false,
    };
    const { rerender } = render(<Composer value="a" {...props} />);
    const textarea = screen.getByRole("textbox", { name: "Mesaj" });
    let contentHeight = 60;
    Object.defineProperty(textarea, "scrollHeight", {
      configurable: true,
      get: () => contentHeight,
    });

    rerender(<Composer value="ab" {...props} />);
    expect(textarea.style.height).toBe("60px");

    contentHeight = 1000;
    rerender(<Composer value={"x\n".repeat(50)} {...props} />);
    expect(textarea.style.height).toBe("192px");
  });
});
