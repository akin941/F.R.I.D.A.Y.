import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { ConversationHeader } from "@/components/chat/ConversationHeader";
import { SourceBadges, connectorSourceLabel } from "@/components/chat/badges";
import { renderWithClient } from "@/test/render";
import type { ApiConnectorRead, ApiConversation } from "@/types/api";

function read(overrides: Partial<ApiConnectorRead> = {}): ApiConnectorRead {
  return { tool: "github_pr_status", source: "github", data_class: "firm", size: 10, truncated: false, ...overrides };
}

describe("chat source badges", () => {
  it.each([
    [read(), "GitHub"],
    [read({ source: "google", tool: "mail_search" }), "Posta"],
    [read({ source: "google", tool: "mail_read" }), "Posta"],
    [read({ source: "google", tool: "calendar_agenda" }), "Takvim"],
    [read({ source: "home_assistant", tool: "ha_states" }), "Ev"],
    [read({ source: "fake", tool: "fake_search" }), "fake"],
  ])("labels %o as %s", (value, label) => {
    expect(connectorSourceLabel(value)).toBe(label);
  });

  it("one badge per distinct source; personal ones in the warning tone", () => {
    render(
      <SourceBadges
        reads={[
          read(),
          read(),
          read({ source: "google", tool: "mail_search", data_class: "personal" }),
        ]}
      />,
    );
    const badges = screen.getAllByTestId("source-badge");
    expect(badges.map((b) => b.textContent)).toEqual(["Kaynak: GitHub", "Kaynak: Posta"]);
    expect(badges[0]).not.toHaveAttribute("data-personal");
    expect(badges[1]).toHaveAttribute("data-personal", "true");
  });

  it("renders nothing without reads", () => {
    const { container } = render(<SourceBadges reads={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("an assistant reply shows its source badge", () => {
    renderWithClient(
      <MessageBubble
        workspaceId="w1"
        message={{
          id: "m1",
          role: "assistant",
          content: "Salon ışığı açık.",
          status: "complete",
          connectorReads: [read({ source: "home_assistant", tool: "ha_states", data_class: "personal" })],
        }}
      />,
    );
    expect(screen.getByTestId("source-badge")).toHaveTextContent("Kaynak: Ev");
  });
});

describe("personal-data notice in the header", () => {
  const conversation: ApiConversation = {
    id: "c1",
    workspace: "w1",
    title: "Ev",
    created_by: null,
    created_at: "2026-11-20T00:00:00Z",
    private: true,
    personal_data: true,
    origin_channel: "web",
  };

  it("a personal conversation shows the private and personal-data badges", () => {
    render(
      <ConversationHeader
        conversation={conversation}
        onOpenList={() => {}}
        onUpdate={async () => {}}
        actionsDisabled={false}
      />,
    );
    expect(screen.getByTestId("private-badge")).toBeInTheDocument();
    expect(screen.getByTestId("personal-data-badge")).toHaveTextContent("Kişisel veri");
  });

  it("an ordinary conversation shows neither", () => {
    render(
      <ConversationHeader
        conversation={{ ...conversation, private: false, personal_data: false }}
        onOpenList={() => {}}
        onUpdate={async () => {}}
        actionsDisabled={false}
      />,
    );
    expect(screen.queryByTestId("personal-data-badge")).not.toBeInTheDocument();
    expect(screen.queryByTestId("private-badge")).not.toBeInTheDocument();
  });
});
