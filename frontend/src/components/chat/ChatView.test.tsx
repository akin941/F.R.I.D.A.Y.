import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatView } from "@/components/chat/ChatView";
import type { ApiWorkspaceSummary } from "@/types/api";

const WORKSPACE_1: ApiWorkspaceSummary = { id: "w1", name: "Alice", type: "personal", role: "owner" };
const WORKSPACE_2: ApiWorkspaceSummary = { id: "w2", name: "Acme", type: "company", role: "member" };

const { workspaceHolder } = vi.hoisted(() => ({
  // A mutable holder, not a fresh literal per call: ChatView's reset effect
  // depends on `activeWorkspace` by identity, so a mock returning a new
  // object every call would make that dependency "change" every render and
  // spin forever. Tests that need to simulate a workspace switch mutate
  // `.current` and call RTL's `rerender` to make ChatView observe it.
  workspaceHolder: { current: { id: "w1", name: "Alice", type: "personal", role: "owner" } } as {
    current: { id: string; name: string; type: "personal" | "company"; role: string };
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspaceHolder.current, user: { id: "u1" } }),
}));

const CONVERSATION = {
  id: "c1",
  workspace: "w1",
  title: "Test conversation",
  created_by: null,
  created_at: "2026-07-05T00:00:00Z",
};

const CONVERSATION_2 = {
  id: "c2",
  workspace: "w2",
  title: "Other workspace conversation",
  created_by: null,
  created_at: "2026-07-05T00:00:00Z",
};

const CONVERSATION_3 = {
  id: "c3",
  workspace: "w1",
  title: "Second conversation",
  created_by: null,
  created_at: "2026-07-05T00:01:00Z",
};

function sseResponse(frames: string[]) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) {
          controller.enqueue(encoder.encode(frame));
        }
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

async function selectConversation() {
  render(<ChatView />);
  const button = await screen.findByText("Test conversation");
  await userEvent.click(button);
  await screen.findByPlaceholderText("Message Jarvis…");
}

describe("ChatView", () => {
  let messagesAfterStream: unknown[];

  beforeEach(() => {
    messagesAfterStream = [];
    workspaceHolder.current = WORKSPACE_1;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an optimistic user message immediately and reconciles after the stream ends", async () => {
    messagesAfterStream = [
      { id: "u1", conversation: "c1", role: "user", content: "Hi there", status: "complete", model_usage: null, created_by: "u1", created_at: "t" },
      { id: "a1", conversation: "c1", role: "assistant", content: "Hello!", status: "complete", model_usage: "mu1", created_by: null, created_at: "t" },
    ];
    let messagesCallCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && (!init?.method || init.method === "GET")) {
          messagesCallCount += 1;
          return new Response(JSON.stringify(messagesCallCount === 1 ? [] : messagesAfterStream), {
            status: 200,
          });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          return sseResponse([
            `data: ${JSON.stringify({ type: "chunk", text: "Hello!" })}\n\n`,
            `data: ${JSON.stringify({ type: "done", message_id: "a1" })}\n\n`,
          ]);
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    await userEvent.type(screen.getByPlaceholderText("Message Jarvis…"), "Hi there");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Hi there")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Hello!")).toBeInTheDocument());
  });

  it("never shows the memory-context indicator on the live streaming bubble, only after reconciliation, and never on user messages", async () => {
    // A user message with a non-empty memories_used never happens in real
    // usage (only assistant replies get memory injected), but deliberately
    // fixturing it this way isolates MessageList's `role === "assistant"`
    // guard from the "is the array non-empty" guard.
    messagesAfterStream = [
      { id: "u1", conversation: "c1", role: "user", content: "Hi there", status: "complete", model_usage: null, created_by: "u1", created_at: "t", memories_used: ["mem-should-not-render"] },
      { id: "a1", conversation: "c1", role: "assistant", content: "Hello!", status: "complete", model_usage: "mu1", created_by: null, created_at: "t", memories_used: ["mem-1", "mem-2"] },
    ];
    let messagesCallCount = 0;
    const encoder = new TextEncoder();
    let pushChunk!: (frame: string) => void;
    let closeStream!: () => void;
    const controlledStream = new ReadableStream<Uint8Array>({
      start(controller) {
        pushChunk = (frame: string) => controller.enqueue(encoder.encode(frame));
        closeStream = () => controller.close();
      },
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && (!init?.method || init.method === "GET")) {
          messagesCallCount += 1;
          return new Response(JSON.stringify(messagesCallCount === 1 ? [] : messagesAfterStream), {
            status: 200,
          });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          return new Response(controlledStream, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          });
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    await userEvent.type(screen.getByPlaceholderText("Message Jarvis…"), "Hi there");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    pushChunk(`data: ${JSON.stringify({ type: "chunk", text: "Hello!" })}\n\n`);
    await waitFor(() => expect(screen.getByText("Hello!")).toBeInTheDocument());
    // Mid-stream: the terminal event hasn't landed yet, so the reply is still
    // the live optimistic bubble — the indicator must not be present yet.
    expect(screen.queryByText(/Memory context/)).not.toBeInTheDocument();

    pushChunk(
      `data: ${JSON.stringify({ type: "done", message_id: "a1", memories_used: ["mem-1", "mem-2"] })}\n\n`,
    );
    closeStream();

    expect(await screen.findByText("Memory context: 2 memories")).toBeInTheDocument();
    // Restricted to assistant messages: the user bubble's fixtured (and
    // unrealistic) memories_used must never render an indicator of its own.
    expect(screen.getAllByText(/Memory context/)).toHaveLength(1);
  });

  it("shows the knowledge-context indicator after reconciliation, mirroring memory context", async () => {
    messagesAfterStream = [
      { id: "u1", conversation: "c1", role: "user", content: "Hi there", status: "complete", model_usage: null, created_by: "u1", created_at: "t", chunks_used: ["chunk-should-not-render"] },
      { id: "a1", conversation: "c1", role: "assistant", content: "Hello!", status: "complete", model_usage: "mu1", created_by: null, created_at: "t", chunks_used: ["chunk-1"] },
    ];
    let messagesCallCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && (!init?.method || init.method === "GET")) {
          messagesCallCount += 1;
          return new Response(JSON.stringify(messagesCallCount === 1 ? [] : messagesAfterStream), {
            status: 200,
          });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          return sseResponse([
            `data: ${JSON.stringify({ type: "chunk", text: "Hello!" })}\n\n`,
            `data: ${JSON.stringify({ type: "done", message_id: "a1", chunks_used: ["chunk-1"] })}\n\n`,
          ]);
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    await userEvent.type(screen.getByPlaceholderText("Message Jarvis…"), "Hi there");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Knowledge context: 1 chunk")).toBeInTheDocument();
    // Restricted to assistant messages, same as memory context.
    expect(screen.getAllByText(/Knowledge context/)).toHaveLength(1);
  });

  it("shows a banner and reconciles when the stream sends an error event", async () => {
    let messagesCallCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          return sseResponse([`data: ${JSON.stringify({ type: "error", error_type: "no_provider" })}\n\n`]);
        }
        if (input === "/api/conversations/c1/messages") {
          messagesCallCount += 1;
          return new Response(JSON.stringify([]), { status: 200 });
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    await userEvent.type(screen.getByPlaceholderText("Message Jarvis…"), "Trigger error");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/no ai provider/i);
    await waitFor(() => expect(messagesCallCount).toBeGreaterThan(0));
  });

  it("shows a disconnect banner when the stream ends without a terminal event", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          return sseResponse([`data: ${JSON.stringify({ type: "chunk", text: "partial" })}\n\n`]);
        }
        if (input === "/api/conversations/c1/messages") {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    await userEvent.type(screen.getByPlaceholderText("Message Jarvis…"), "Disconnect me");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/connection lost/i)).toBeInTheDocument();
  });

  it("blocks a second send while a stream is already in flight", async () => {
    let resolveStream: (() => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          const encoder = new TextEncoder();
          return new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                resolveStream = () => {
                  controller.enqueue(
                    encoder.encode(`data: ${JSON.stringify({ type: "done", message_id: "a1" })}\n\n`),
                  );
                  controller.close();
                };
              },
            }),
            { status: 200 },
          );
        }
        if (input === "/api/conversations/c1/messages") {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    const textarea = screen.getByPlaceholderText("Message Jarvis…");
    await userEvent.type(textarea, "First message");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    expect(textarea).toBeDisabled();

    resolveStream?.();
  });

  it("does not let a stale response from a previous workspace overwrite the new workspace's conversation list", async () => {
    let resolveWorkspace1: ((response: Response) => void) | undefined;
    const workspace1Pending = new Promise<Response>((resolve) => {
      resolveWorkspace1 = resolve;
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input === "/api/conversations?workspace=w1") {
          return workspace1Pending;
        }
        if (input === "/api/conversations?workspace=w2") {
          return new Response(JSON.stringify([CONVERSATION_2]), { status: 200 });
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    const { rerender } = render(<ChatView />);
    // w1's conversations request is still in flight when the user switches
    // workspaces — this must not be allowed to land afterwards and clobber
    // whatever w2 loaded.
    workspaceHolder.current = WORKSPACE_2;
    rerender(<ChatView />);

    await screen.findByText("Other workspace conversation");

    resolveWorkspace1?.(new Response(JSON.stringify([CONVERSATION]), { status: 200 }));

    // Give the stale w1 response a chance to resolve and (incorrectly) apply.
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(screen.queryByText("Test conversation")).not.toBeInTheDocument();
    expect(screen.getByText("Other workspace conversation")).toBeInTheDocument();
  });

  it("does not let a deferred messages response for a previous conversation overwrite the newly-selected one", async () => {
    let resolveConversation1Messages: ((response: Response) => void) | undefined;
    const conversation1MessagesPending = new Promise<Response>((resolve) => {
      resolveConversation1Messages = resolve;
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION, CONVERSATION_3]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages") {
          return conversation1MessagesPending;
        }
        if (input === "/api/conversations/c3/messages") {
          return new Response(
            JSON.stringify([
              {
                id: "m3",
                conversation: "c3",
                role: "user",
                content: "Message in second conversation",
                status: "complete",
                model_usage: null,
                created_by: "u1",
                created_at: "t",
              },
            ]),
            { status: 200 },
          );
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    render(<ChatView />);
    const firstButton = await screen.findByText("Test conversation");
    fireEvent.click(firstButton);
    // c1's messages request is still in flight when the user selects c3 —
    // that must not be allowed to land afterwards and clobber c3's messages.
    const secondButton = await screen.findByText("Second conversation");
    fireEvent.click(secondButton);

    await screen.findByText("Message in second conversation");

    resolveConversation1Messages?.(
      new Response(
        JSON.stringify([
          {
            id: "m1",
            conversation: "c1",
            role: "user",
            content: "Message in first conversation",
            status: "complete",
            model_usage: null,
            created_by: "u1",
            created_at: "t",
          },
        ]),
        { status: 200 },
      ),
    );

    // Give the stale c1 response a chance to resolve and (incorrectly) apply.
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(screen.queryByText("Message in first conversation")).not.toBeInTheDocument();
    expect(screen.getByText("Message in second conversation")).toBeInTheDocument();
  });

  it("aborts an in-flight stream when the workspace changes mid-send", async () => {
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
    let streamAborted = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          init?.signal?.addEventListener("abort", () => {
            streamAborted = true;
          });
          return new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                streamController = controller;
              },
              cancel() {
                streamAborted = true;
              },
            }),
            { status: 200 },
          );
        }
        if (input === "/api/conversations/c1/messages") {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    const { rerender } = render(<ChatView />);
    const button = await screen.findByText("Test conversation");
    await userEvent.click(button);
    const textarea = await screen.findByPlaceholderText("Message Jarvis…");
    await userEvent.type(textarea, "First message");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument());

    workspaceHolder.current = WORKSPACE_2;
    rerender(<ChatView />);

    await waitFor(() => expect(streamAborted).toBe(true));
    // Once aborted, the workspace switch's own placeholder view takes over —
    // no residual "Stop" button stuck from the abandoned stream.
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();

    streamController?.close();
  });

  it("sends exactly one POST when Send is clicked twice in the same tick", async () => {
    let postCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          postCalls += 1;
          return sseResponse([`data: ${JSON.stringify({ type: "done", message_id: "a1" })}\n\n`]);
        }
        if (input === "/api/conversations/c1/messages") {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    await userEvent.type(screen.getByPlaceholderText("Message Jarvis…"), "Double click");
    const sendButton = screen.getByRole("button", { name: "Send" });
    // Both clicks inside one `act` callback run before React flushes any
    // resulting state update, so the second click's handler sees exactly the
    // same pre-click state as the first — the genuine same-tick scenario, not
    // two clicks each allowed their own render in between.
    act(() => {
      fireEvent.click(sendButton);
      fireEvent.click(sendButton);
    });

    await waitFor(() => expect(postCalls).toBe(1));
  });

  it("shows a task draft card on a draft event and confirms it via POST /api/tasks", async () => {
    let createCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          return sseResponse([
            `data: ${JSON.stringify({ type: "chunk", text: "Sure, I'll draft that." })}\n\n`,
            `data: ${JSON.stringify({
              type: "draft",
              kind: "task",
              source_message_id: "u1",
              title: "Call mom",
              due_date: "2026-07-15",
            })}\n\n`,
            `data: ${JSON.stringify({ type: "done", message_id: "a1" })}\n\n`,
          ]);
        }
        if (input === "/api/conversations/c1/messages") {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        if (input === "/api/tasks" && init?.method === "POST") {
          createCalls += 1;
          expect(JSON.parse(init.body as string)).toMatchObject({
            workspace: "w1",
            title: "Call mom",
            due_date: "2026-07-15",
            draft_message_id: "u1",
          });
          return new Response(
            JSON.stringify({
              id: "t1",
              workspace: "w1",
              title: "Call mom",
              description: "",
              status: "open",
              priority: "normal",
              due_date: "2026-07-15",
              source: "ai_suggested",
              created_by: "u1",
              created_at: "t",
              updated_at: "t",
            }),
            { status: 201 },
          );
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    await userEvent.type(screen.getByPlaceholderText("Message Jarvis…"), "remind me to call mom");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Looks like a task")).toBeInTheDocument();
    expect(screen.getByText("Call mom")).toBeInTheDocument();
    expect(screen.getByText("Due 2026-07-15")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Confirm task" }));

    await waitFor(() => expect(createCalls).toBe(1));
    await waitFor(() => expect(screen.queryByText("Looks like a task")).not.toBeInTheDocument());
  });

  it("discards a task draft card without ever calling POST /api/tasks", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          return sseResponse([
            `data: ${JSON.stringify({
              type: "draft",
              kind: "task",
              source_message_id: "u1",
              title: "Call mom",
              due_date: null,
            })}\n\n`,
            `data: ${JSON.stringify({ type: "done", message_id: "a1" })}\n\n`,
          ]);
        }
        if (input === "/api/conversations/c1/messages") {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        if (input === "/api/tasks") {
          throw new Error("must not call /api/tasks on discard");
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    await userEvent.type(screen.getByPlaceholderText("Message Jarvis…"), "remind me to call mom");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Looks like a task")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));

    expect(screen.queryByText("Looks like a task")).not.toBeInTheDocument();
  });

  it("shows a tool request card on a draft event and confirms it via POST /api/approvals/requests", async () => {
    let createCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          return sseResponse([
            `data: ${JSON.stringify({
              type: "draft",
              kind: "tool",
              source_message_id: "u1",
              tool_name: "vault_export",
              payload: {},
            })}\n\n`,
            `data: ${JSON.stringify({ type: "done", message_id: "a1" })}\n\n`,
          ]);
        }
        if (input === "/api/conversations/c1/messages") {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        if (input === "/api/approvals/requests" && init?.method === "POST") {
          createCalls += 1;
          expect(JSON.parse(init.body as string)).toMatchObject({
            workspace: "w1",
            tool_name: "vault_export",
            payload: {},
          });
          return new Response(
            JSON.stringify({
              id: "ar1",
              workspace: "w1",
              tool_name: "vault_export",
              status: "pending",
              // "u1" matches the mocked useWorkspace() user id above -- the
              // requester is always the current user in this flow, so this
              // proves ApprovalCard's OWN isOwnRequest gating (not new logic
              // here) is what hides Approve/Reject (Codex round-1 review).
              requested_by: "u1",
              decided_by: null,
              decided_at: null,
              created_at: "t",
              updated_at: "t",
              execution: null,
            }),
            { status: 201 },
          );
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    await userEvent.type(screen.getByPlaceholderText("Message Jarvis…"), "export my vault");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Looks like a tool request")).toBeInTheDocument();
    expect(screen.getByText("vault_export")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Confirm request" }));

    await waitFor(() => expect(createCalls).toBe(1));
    await waitFor(() =>
      expect(screen.queryByText("Looks like a tool request")).not.toBeInTheDocument(),
    );
    // Confirming renders the shared ApprovalCard (status view), reusing the
    // exact component /approvals uses for run-status rendering (brief §4).
    expect(await screen.findByText("Pending")).toBeInTheDocument();
    expect(
      screen.getByText("Waiting for another workspace admin to decide this request."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("shows an error and lets the user retry when confirming a tool request fails", async () => {
    let createCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          return sseResponse([
            `data: ${JSON.stringify({
              type: "draft",
              kind: "tool",
              source_message_id: "u1",
              tool_name: "vault_export",
              payload: {},
            })}\n\n`,
            `data: ${JSON.stringify({ type: "done", message_id: "a1" })}\n\n`,
          ]);
        }
        if (input === "/api/conversations/c1/messages") {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        if (input === "/api/approvals/requests" && init?.method === "POST") {
          createCalls += 1;
          return new Response(JSON.stringify({ detail: "Role does not allow this action." }), {
            status: 403,
          });
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    await userEvent.type(screen.getByPlaceholderText("Message Jarvis…"), "export my vault");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Looks like a tool request")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm request" }));

    expect(await screen.findByText("Role does not allow this action.")).toBeInTheDocument();
    await waitFor(() => expect(createCalls).toBe(1));
    // The card must still be here (not silently dismissed) so the user can
    // discard or retry.
    expect(screen.getByText("Looks like a tool request")).toBeInTheDocument();
  });

  it("discards a tool request card without ever calling POST /api/approvals/requests", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([CONVERSATION]), { status: 200 });
        }
        if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
          return sseResponse([
            `data: ${JSON.stringify({
              type: "draft",
              kind: "tool",
              source_message_id: "u1",
              tool_name: "vault_export",
              payload: {},
            })}\n\n`,
            `data: ${JSON.stringify({ type: "done", message_id: "a1" })}\n\n`,
          ]);
        }
        if (input === "/api/conversations/c1/messages") {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        if (input === "/api/approvals/requests") {
          throw new Error("must not call /api/approvals/requests on discard");
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    await selectConversation();
    await userEvent.type(screen.getByPlaceholderText("Message Jarvis…"), "export my vault");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Looks like a tool request")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));

    expect(screen.queryByText("Looks like a tool request")).not.toBeInTheDocument();
  });

  it("creates exactly one conversation when New chat is clicked twice in the same tick", async () => {
    let createCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input.startsWith("/api/conversations?")) {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        if (input === "/api/conversations" && init?.method === "POST") {
          createCalls += 1;
          return new Response(JSON.stringify(CONVERSATION), { status: 201 });
        }
        if (input === "/api/conversations/c1/messages") {
          return new Response(JSON.stringify([]), { status: 200 });
        }
        throw new Error(`unexpected fetch ${input}`);
      }),
    );

    render(<ChatView />);
    const newChatButton = await screen.findByText("New chat");
    act(() => {
      fireEvent.click(newChatButton);
      fireEvent.click(newChatButton);
    });

    await waitFor(() => expect(createCalls).toBe(1));
  });
});
