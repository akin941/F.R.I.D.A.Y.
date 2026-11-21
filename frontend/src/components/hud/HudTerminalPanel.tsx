"use client";

import { useEffect, useState } from "react";
import { Copy } from "lucide-react";
import type { HudTerminal, HudTerminalItem } from "@/lib/hud/events";
import {
  TerminalStream,
  terminalStreamUrl,
  type TerminalChunk,
  type TerminalFinal,
  type TerminalStreamCallbacks,
  type TerminalStreamStatus,
} from "@/lib/hud/terminal";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";

/**
 * The HUD terminal panel (Phase 10 D12): the owner's last five Mac actions
 * (from the HUD event stream's `terminal` section) and the scrubbed output
 * of the selected one, streamed from `/api/hud/terminal/<id>/stream`.
 *
 * The output is UNTRUSTED text (the label says so): it is rendered only as
 * React text nodes inside one `<pre>` -- never as HTML, never as Markdown,
 * no ANSI interpretation (escape sequences are stripped server-side), no
 * links. Read-only: nothing here starts, stops or decides anything; "Durdur"
 * is a button on the Mac's own window (D1).
 */

export type TerminalStreamFactory = (
  url: string,
  callbacks: TerminalStreamCallbacks,
) => Pick<TerminalStream, "start" | "stop">;

const defaultFactory: TerminalStreamFactory = (url, callbacks) => new TerminalStream(url, callbacks);

const PANEL = "flex min-w-0 flex-col gap-2 rounded-xl border border-border/60 p-3 sm:p-4";
const HEADING = "text-xs font-semibold tracking-wide text-hud-caption-muted uppercase";
// The stream is bounded server-side (1 MiB); the panel keeps the last part.
const MAX_SHOWN_CHARS = 200_000;

function statusText(status: string): string {
  return tr.hud.terminal.statuses[status] ?? status;
}

function pickDefault(items: HudTerminalItem[]): string | null {
  return (items.find((item) => item.running) ?? items[0])?.id ?? null;
}

function ItemLine({ item }: { item: HudTerminalItem }) {
  const parts = [item.label];
  if (item.repo_name) parts.push(item.repo_name);
  parts.push(statusText(item.status));
  if (item.exit_code !== null) parts.push(tr.hud.terminal.exitCode(item.exit_code));
  if (item.terminal) parts.push(tr.hud.terminal.duration(Math.round(item.duration_ms / 1000)));
  return <span className="truncate">{parts.join(" · ")}</span>;
}

function TerminalOutput({
  executionId,
  factory,
}: {
  executionId: string;
  factory: TerminalStreamFactory;
}) {
  const [chunks, setChunks] = useState<TerminalChunk[]>([]);
  const [final, setFinal] = useState<TerminalFinal | null>(null);
  const [status, setStatus] = useState<TerminalStreamStatus>("connecting");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const stream = factory(terminalStreamUrl(executionId), {
      onChunk: (chunk) => setChunks((previous) => [...previous, chunk]),
      onFinal: setFinal,
      onStatus: setStatus,
    });
    stream.start();
    return () => stream.stop();
  }, [executionId, factory]);

  let text = chunks.map((chunk) => chunk.text).join("");
  if (text.length > MAX_SHOWN_CHARS) text = text.slice(-MAX_SHOWN_CHARS);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-hud-caption-muted" data-testid="hud-terminal-untrusted">
          {tr.hud.terminal.untrusted}
        </p>
        <button
          type="button"
          onClick={() => void copy()}
          disabled={!text}
          className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs text-hud-caption-muted hover:text-hud-caption disabled:opacity-50"
        >
          <Copy aria-hidden className="size-3" />
          {copied ? tr.hud.terminal.copied : tr.hud.terminal.copy}
        </button>
      </div>
      <p className="sr-only">{tr.hud.terminal.untrustedHint}</p>
      {/* Text nodes only: the output is untrusted data (D12). */}
      <pre
        data-testid="hud-terminal-output"
        aria-label={tr.hud.terminal.untrusted}
        className="max-h-64 overflow-auto rounded-md bg-black/40 p-2 font-mono text-xs leading-snug break-words whitespace-pre-wrap text-hud-caption"
      >
        {text || (final ? tr.hud.terminal.noOutput : tr.hud.terminal.waiting)}
      </pre>
      {status === "lost" ? (
        <p className="text-xs text-hud-caption-muted" role="status">
          {tr.hud.terminal.lost}
        </p>
      ) : null}
      {final ? (
        <p className="text-xs" role="status" data-testid="hud-terminal-final">
          {tr.hud.terminal.final(statusText(final.status))}
          {final.exit_code !== null ? ` · ${tr.hud.terminal.exitCode(final.exit_code)}` : ""}
          {final.truncated ? ` · ${tr.hud.terminal.truncated}` : ""}
        </p>
      ) : null}
    </div>
  );
}

export function HudTerminalPanel({
  terminal,
  streamFactory = defaultFactory,
}: {
  terminal: HudTerminal | null;
  streamFactory?: TerminalStreamFactory;
}) {
  const items = terminal?.items ?? [];
  const [chosen, setChosen] = useState<string | null>(null);
  const selected = chosen && items.some((item) => item.id === chosen) ? chosen : pickDefault(items);

  return (
    <section className={PANEL} aria-labelledby="hud-terminal" data-testid="hud-terminal">
      <h2 id="hud-terminal" className={HEADING}>
        {tr.hud.terminal.heading}
      </h2>
      {items.length === 0 ? (
        <p className="text-sm text-hud-caption-muted">{tr.hud.terminal.empty}</p>
      ) : (
        <>
          <ul className="flex flex-col gap-1 text-xs">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  aria-pressed={item.id === selected}
                  aria-label={`${tr.hud.terminal.select}: ${item.label}`}
                  onClick={() => setChosen(item.id)}
                  data-testid="hud-terminal-item"
                  className={cn(
                    "flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1 text-left",
                    item.id === selected ? "bg-white/10 text-hud-caption" : "text-hud-caption-muted",
                  )}
                >
                  <ItemLine item={item} />
                </button>
              </li>
            ))}
          </ul>
          {selected ? <TerminalOutput key={selected} executionId={selected} factory={streamFactory} /> : null}
        </>
      )}
    </section>
  );
}
