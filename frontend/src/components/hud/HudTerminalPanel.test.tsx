import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { HudTerminalPanel, type TerminalStreamFactory } from "@/components/hud/HudTerminalPanel";
import type { HudTerminal, HudTerminalItem } from "@/lib/hud/events";
import type { TerminalStreamCallbacks } from "@/lib/hud/terminal";
import { tr } from "@/lib/i18n/tr";

function item(overrides: Partial<HudTerminalItem> = {}): HudTerminalItem {
  return {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    capability: "repo.command.run",
    label: "Repoda komut çalıştır",
    class: "gate",
    repo_name: "acme",
    status: "ok",
    running: false,
    terminal: true,
    exit_code: 0,
    duration_ms: 4200,
    created_at: "2026-11-20T10:00:00Z",
    finished_at: "2026-11-20T10:00:04Z",
    ...overrides,
  };
}

function setup(terminal: HudTerminal | null) {
  const streams: { url: string; callbacks: TerminalStreamCallbacks; stop: ReturnType<typeof vi.fn> }[] = [];
  const factory: TerminalStreamFactory = (url, callbacks) => {
    const stop = vi.fn();
    streams.push({ url, callbacks, stop });
    return { start: vi.fn(), stop };
  };
  const view = render(<HudTerminalPanel terminal={terminal} streamFactory={factory} />);
  return { ...view, streams };
}

afterEach(() => vi.restoreAllMocks());

describe("HudTerminalPanel (Phase 10 D12)", () => {
  it("says nothing ran yet when the section is empty, and opens no stream", () => {
    const h = setup({ items: [], running_count: 0 });
    expect(screen.getByText(tr.hud.terminal.empty)).toBeInTheDocument();
    expect(h.streams).toHaveLength(0);
  });

  it("streams the running execution by default, labelled untrusted", () => {
    const running = item({ id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", status: "running", running: true, terminal: false, exit_code: null });
    const h = setup({ items: [item(), running], running_count: 1 });
    expect(h.streams).toHaveLength(1);
    expect(h.streams[0]!.url).toBe(`/api/hud/terminal/${running.id}/stream`);
    expect(screen.getByTestId("hud-terminal-untrusted")).toHaveTextContent(tr.hud.terminal.untrusted);
    expect(screen.getByTestId("hud-terminal-output")).toHaveTextContent(tr.hud.terminal.waiting);
  });

  it("renders output as text only: markup shows literally, nothing is parsed", () => {
    const h = setup({ items: [item()], running_count: 0 });
    const hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script><b>bold</b>\n';
    act(() => {
      h.streams[0]!.callbacks.onChunk({ seq: 0, stream: "stdout", text: hostile });
      h.streams[0]!.callbacks.onChunk({ seq: 1, stream: "stderr", text: "second line\n" });
    });
    const pre = screen.getByTestId("hud-terminal-output");
    expect(pre.tagName).toBe("PRE");
    expect(pre.textContent).toBe(hostile + "second line\n");
    expect(pre.querySelector("img, script, b")).toBeNull();
    expect(document.querySelector("img[src='x']")).toBeNull();
    expect(pre.children).toHaveLength(0);
  });

  it("shows the final status line with exit code and truncation", () => {
    const h = setup({ items: [item({ status: "failed", exit_code: 1 })], running_count: 0 });
    act(() => {
      h.streams[0]!.callbacks.onChunk({ seq: 0, stream: "stdout", text: "FAILED test_login\n" });
      h.streams[0]!.callbacks.onFinal({ status: "failed", exit_code: 1, truncated: true });
    });
    const final = screen.getByTestId("hud-terminal-final");
    expect(final).toHaveTextContent(tr.hud.terminal.final(tr.hud.terminal.statuses.failed!));
    expect(final).toHaveTextContent(tr.hud.terminal.exitCode(1));
    expect(final).toHaveTextContent(tr.hud.terminal.truncated);
  });

  it("switches the stream when another execution is chosen, stopping the old one", () => {
    const other = item({ id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", repo_name: "friday" });
    const h = setup({ items: [item(), other], running_count: 0 });
    fireEvent.click(screen.getAllByTestId("hud-terminal-item")[1]!);
    expect(h.streams).toHaveLength(2);
    expect(h.streams[0]!.stop).toHaveBeenCalled();
    expect(h.streams[1]!.url).toContain(other.id);
  });

  it("copies the shown output", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const h = setup({ items: [item()], running_count: 0 });
    act(() => h.streams[0]!.callbacks.onChunk({ seq: 0, stream: "stdout", text: "3 passed\n" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: tr.hud.terminal.copy }));
    });
    expect(writeText).toHaveBeenCalledWith("3 passed\n");
    expect(screen.getByRole("button", { name: tr.hud.terminal.copied })).toBeInTheDocument();
  });

  it("lists label, repo name, status, exit code and duration -- never a path", () => {
    setup({ items: [item()], running_count: 0 });
    const line = screen.getByTestId("hud-terminal-item");
    expect(line).toHaveTextContent("Repoda komut çalıştır · acme · başarılı · çıkış kodu 0 · 4 sn");
  });
});
