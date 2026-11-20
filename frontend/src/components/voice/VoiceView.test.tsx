import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VoiceView } from "@/components/voice/VoiceView";

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: { id: "w1", name: "Alice", type: "personal", role: "owner" } }),
}));

const CONVERSATION = {
  id: "c1",
  workspace: "w1",
  title: "Sesli",
  created_by: null,
  created_at: "2026-07-16T00:00:00Z",
};

function sseResponse(frames: string[]) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

describe("VoiceView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("streams a reply and returns the orb to idle when the round-trip completes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/conversations") {
          return new Response(JSON.stringify(CONVERSATION), { status: 201 });
        }
        if (url === "/api/conversations/c1/messages") {
          return sseResponse([
            'data: {"type":"chunk","text":"Merhaba"}\n\n',
            'data: {"type":"done","message_id":"a1","memories_used":[],"chunks_used":[]}\n\n',
          ]);
        }
        throw new Error(`Unexpected fetch: ${url}`);
      }),
    );

    render(<VoiceView />);
    expect(screen.getByRole("img", { name: "Friday boşta" })).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText("Friday'ye yaz…"), "Merhaba Friday");
    await userEvent.click(screen.getByRole("button", { name: "Gönder" }));

    expect(await screen.findByText("Merhaba")).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "Friday boşta" })).toBeInTheDocument();
  });

  it("returns the orb to idle and shows an error banner on a stream error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/conversations") {
          return new Response(JSON.stringify(CONVERSATION), { status: 201 });
        }
        if (url === "/api/conversations/c1/messages") {
          return sseResponse(['data: {"type":"error","error_type":"no_provider"}\n\n']);
        }
        throw new Error(`Unexpected fetch: ${url}`);
      }),
    );

    render(<VoiceView />);
    await userEvent.type(screen.getByPlaceholderText("Friday'ye yaz…"), "Merhaba Friday");
    await userEvent.click(screen.getByRole("button", { name: "Gönder" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Şu anda yapılandırılmış bir yapay zeka sağlayıcısı yok. Lütfen daha sonra tekrar deneyin.",
    );
    expect(await screen.findByRole("img", { name: "Friday boşta" })).toBeInTheDocument();
  });
});
