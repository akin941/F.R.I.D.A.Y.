import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { TaskView } from "@/components/tasks/TaskView";

// A stable object, not a fresh literal per call: views depend on
// `activeWorkspace` by identity, so a new object every render would re-run
// their fetch effect forever.
const { workspace } = vi.hoisted(() => ({
  workspace: { id: "w1", name: "Alice", type: "personal" as const, role: "owner" },
}));

vi.mock("@/contexts/workspace-context", () => ({
  useWorkspace: () => ({ activeWorkspace: workspace }),
}));

const COMPONENTS_DIR = path.resolve(__dirname, "..");

function componentSources(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      // shadcn primitives (popover menus) scroll themselves; not views.
      return entry === "ui" ? [] : componentSources(full);
    }
    return full.endsWith(".tsx") && !full.endsWith(".test.tsx") ? [full] : [];
  });
}

describe("view scroll root contract", () => {
  it("keeps every direct child from shrinking inside the bounded scroll root", () => {
    const classes = VIEW_SCROLL_ROOT.split(" ");
    expect(classes).toEqual(
      expect.arrayContaining(["flex-1", "min-h-0", "overflow-y-auto", "*:shrink-0"]),
    );
  });

  it("is the only way a shell view declares a vertical scroll region", () => {
    // A hand-rolled `overflow-y-auto` view root would reintroduce the
    // collapsed-card bug (a Card is overflow-hidden, so it shrinks to ~0
    // inside a fixed-height flex column). The sidebar is the one exception:
    // its single child is a full-height column, not a stack of cards.
    const offenders = componentSources(COMPONENTS_DIR)
      .filter((file) => !file.endsWith(path.join("shell", "Sidebar.tsx")))
      .filter((file) => readFileSync(file, "utf8").includes("overflow-y-auto"))
      .map((file) => path.relative(COMPONENTS_DIR, file));
    expect(offenders).toEqual([]);
  });

  it("is applied to a real view root (TaskView)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify([]), { status: 200 })),
    );

    render(<TaskView />);
    const heading = await screen.findByRole("heading", { name: "Görevler" });
    const root = heading.closest(".overflow-y-auto");

    expect(root).not.toBeNull();
    for (const cls of VIEW_SCROLL_ROOT.split(" ")) {
      expect(root).toHaveClass(cls);
    }
    vi.unstubAllGlobals();
  });
});
