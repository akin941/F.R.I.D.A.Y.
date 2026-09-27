import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fireEvent } from "@testing-library/react";
import { MessageList } from "@/components/chat/MessageList";
import { isNearBottom, NEAR_BOTTOM_PX } from "@/components/chat/scroll";
import type { ChatMessageView } from "@/components/chat/types";

describe("isNearBottom", () => {
  it("follows within the threshold and not beyond it", () => {
    const at = (scrollTop: number) => ({ scrollTop, scrollHeight: 1000, clientHeight: 400 });
    expect(isNearBottom(at(600))).toBe(true); // at the bottom
    expect(isNearBottom(at(600 - NEAR_BOTTOM_PX))).toBe(true); // exactly 80px up
    expect(isNearBottom(at(600 - NEAR_BOTTOM_PX - 1))).toBe(false);
    expect(isNearBottom(at(0))).toBe(false);
  });
});

function messages(count: number, lastText = "son"): ChatMessageView[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `m${i}`,
    role: i % 2 === 0 ? "user" : "assistant",
    content: i === count - 1 ? lastText : `mesaj ${i}`,
    status: "complete",
  }));
}

/**
 * jsdom has no layout: give the scroll container a fake geometry whose
 * scrollHeight grows with the rendered message count, and a writable
 * scrollTop.
 */
function fakeGeometry(el: HTMLElement) {
  let scrollTop = 0;
  const perMessage = 100;
  Object.defineProperty(el, "clientHeight", { configurable: true, get: () => 400 });
  Object.defineProperty(el, "scrollHeight", {
    configurable: true,
    get: () =>
      el.querySelectorAll("[data-testid$='-message']").length * perMessage +
      el.textContent!.length,
  });
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => scrollTop,
    set: (v: number) => {
      scrollTop = v;
    },
  });
}

function renderList(list: ChatMessageView[], followSignal = 0) {
  return render(<MessageList messages={list} workspaceId="w1" followSignal={followSignal} />);
}

describe("MessageList smart scroll", () => {
  it("follows new content while the reader is at the bottom", () => {
    const { rerender } = renderList(messages(6));
    const scroller = screen.getByTestId("message-scroll");
    fakeGeometry(scroller);
    scroller.scrollTop = scroller.scrollHeight - 400;
    fireEvent.scroll(scroller);

    rerender(<MessageList messages={messages(8)} workspaceId="w1" followSignal={0} />);

    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
    expect(screen.queryByRole("button", { name: "Yeni mesaj ↓" })).not.toBeInTheDocument();
  });

  it("keeps a reader who scrolled up in place and shows the new-message pill", async () => {
    const { rerender } = renderList(messages(6));
    const scroller = screen.getByTestId("message-scroll");
    fakeGeometry(scroller);
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);

    rerender(<MessageList messages={messages(8)} workspaceId="w1" followSignal={0} />);

    expect(scroller.scrollTop).toBe(0);
    const pill = screen.getByRole("button", { name: "Yeni mesaj ↓" });

    await userEvent.click(pill);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
    expect(screen.queryByRole("button", { name: "Yeni mesaj ↓" })).not.toBeInTheDocument();

    // Following again: the next growth scrolls without a pill.
    rerender(<MessageList messages={messages(10)} workspaceId="w1" followSignal={0} />);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
  });

  it("drops the pill when the reader scrolls back down themselves", () => {
    const { rerender } = renderList(messages(6));
    const scroller = screen.getByTestId("message-scroll");
    fakeGeometry(scroller);
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);
    rerender(<MessageList messages={messages(8)} workspaceId="w1" followSignal={0} />);
    expect(screen.getByRole("button", { name: "Yeni mesaj ↓" })).toBeInTheDocument();

    scroller.scrollTop = scroller.scrollHeight - 400 - 10;
    fireEvent.scroll(scroller);
    expect(screen.queryByRole("button", { name: "Yeni mesaj ↓" })).not.toBeInTheDocument();
  });

  it("streamed growth of the last message also counts as new content", () => {
    const { rerender } = renderList(messages(6, "Bir"));
    const scroller = screen.getByTestId("message-scroll");
    fakeGeometry(scroller);
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);

    rerender(
      <MessageList messages={messages(6, "Bir varmış bir yokmuş")} workspaceId="w1" followSignal={0} />,
    );
    expect(screen.getByRole("button", { name: "Yeni mesaj ↓" })).toBeInTheDocument();
  });

  it("an unrelated re-render (same content) never raises the pill", () => {
    const list = messages(6);
    const { rerender } = renderList(list);
    const scroller = screen.getByTestId("message-scroll");
    fakeGeometry(scroller);
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);

    // Same content, new array/object identities (e.g. a composer keystroke).
    rerender(<MessageList messages={messages(6)} workspaceId="w1" followSignal={0} />);
    expect(screen.queryByRole("button", { name: "Yeni mesaj ↓" })).not.toBeInTheDocument();
  });

  it("sending (a new follow signal) always jumps back to the bottom", () => {
    const { rerender } = renderList(messages(6));
    const scroller = screen.getByTestId("message-scroll");
    fakeGeometry(scroller);
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);

    rerender(<MessageList messages={messages(7)} workspaceId="w1" followSignal={1} />);
    expect(scroller.scrollTop).toBe(scroller.scrollHeight);
    expect(screen.queryByRole("button", { name: "Yeni mesaj ↓" })).not.toBeInTheDocument();
  });
});
