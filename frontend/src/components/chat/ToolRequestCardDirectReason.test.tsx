import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { ToolRequestCard, directReasonText, type ToolRequestDraft } from "@/components/chat/ToolRequestCard";
import { toolDraftFromMessage } from "@/components/chat/drafts";
import { SSEStreamParser } from "@/lib/sse";
import { renderWithClient } from "@/test/render";
import type { ChatMessageView } from "@/components/chat/types";

// Phase 13b D7 (ADR-018): a Home Assistant card that was not direct says why
// -- only for the reasons that concern a flagged or flaggable media player.

const WS = "11111111-1111-4111-8111-111111111111";
const MSG = "33333333-3333-4333-8333-333333333333";

function ha(directReason?: string, toolName = "ha_call_service"): ToolRequestDraft {
  return {
    sourceMessageId: MSG,
    toolName,
    payload: { domain: "media_player", service: "media_pause", entity_id: "media_player.salon_tv" },
    ...(directReason ? { directReason } : {}),
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("the card's direct reason line (Phase 13b D7)", () => {
  it.each([
    ["not_flagged", "işaretli değil"],
    ["kill_switch_owner", "doğrudan medya kapalı"],
    ["kill_switch_env", "doğrudan medya ortam ayarıyla kapalı"],
    ["rate_limited", "dakikalık sınır aşıldı"],
    ["volume_at_max", "ses en yüksek düzeye yakın"],
    ["target_unavailable", "cihaz şu an doğrulanamadı"],
  ])("shows %s", (reason, text) => {
    renderWithClient(
      <ToolRequestCard draft={ha(reason)} workspaceId={WS} created={null} onCreated={vi.fn()} onDiscard={vi.fn()} />,
    );
    expect(screen.getByTestId("tool-draft-direct-reason")).toHaveTextContent(`Doğrudan değil: ${text}`);
  });

  it.each(["not_media_player", "service_not_direct", "origin_not_direct", "entity_not_allowed", "unknown_code"])(
    "stays silent for %s",
    (reason) => {
      expect(directReasonText(ha(reason))).toBeNull();
    },
  );

  it("is never shown on another tool's card or without a reason", () => {
    expect(directReasonText(ha("not_flagged", "node_action"))).toBeNull();
    expect(directReasonText(ha())).toBeNull();
  });

  it("the stream parser keeps a closed code and drops anything else", () => {
    const base = { type: "draft", kind: "tool", source_message_id: MSG, tool_name: "ha_call_service", payload: {} };
    const parse = (body: unknown) =>
      new SSEStreamParser().push(new TextEncoder().encode(`data: ${JSON.stringify(body)}\n\n`))[0];
    expect(parse({ ...base, direct_reason: "not_flagged" })).toMatchObject({ direct_reason: "not_flagged" });
    expect(parse({ ...base, direct_reason: "<b>x</b>" })).not.toHaveProperty("direct_reason");
    expect(parse(base)).not.toHaveProperty("direct_reason");
  });

  it("a persisted card keeps the reason", () => {
    const message = {
      id: MSG,
      role: "user",
      content: "TV'yi duraklat",
      channel: "web",
      intent: "tool_request",
      intentPayload: { tool_name: "ha_call_service", payload: {}, direct_reason: "kill_switch_owner" },
    } as unknown as ChatMessageView;
    expect(toolDraftFromMessage(message)?.directReason).toBe("kill_switch_owner");
  });
});
