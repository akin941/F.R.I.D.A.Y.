import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { toView } from "@/components/chat/types";
import { renderWithClient } from "@/test/render";
import type { ApiMessage } from "@/types/api";

function apiMessage(overrides: Partial<ApiMessage>): ApiMessage {
  return {
    id: "m1",
    conversation: "c1",
    role: "user",
    content: "Yarın hava nasıl?",
    status: "complete",
    channel: "web",
    created_at: "2026-11-20T10:00:00Z",
    ...overrides,
  } as ApiMessage;
}

describe("voice turn badge in chat history (Phase 6 branch 3)", () => {
  it("a spoken user turn shows the mic badge", () => {
    renderWithClient(
      <MessageBubble message={toView(apiMessage({ input_mode: "voice" }))} workspaceId="w1" />,
    );
    expect(screen.getByTestId("voice-badge")).toHaveTextContent("Sesli");
    expect(screen.getByTestId("voice-badge")).toHaveAttribute("title", "Bu mesaj sesle söylendi");
  });

  it("a typed turn (or a row predating the field) shows none", () => {
    renderWithClient(
      <MessageBubble message={toView(apiMessage({ input_mode: "text" }))} workspaceId="w1" />,
    );
    renderWithClient(<MessageBubble message={toView(apiMessage({ id: "m2" }))} workspaceId="w1" />);
    expect(screen.queryByTestId("voice-badge")).not.toBeInTheDocument();
  });
});
