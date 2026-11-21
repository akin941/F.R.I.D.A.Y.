import { describe, expect, it } from "vitest";
import { activeNavHref, NAV_GROUPS } from "@/components/shell/nav";

describe("activeNavHref (Phase 8A branch 6)", () => {
  it("lists the sessions page and the morning card", () => {
    const hrefs = NAV_GROUPS.flatMap((g) => g.items.map((i) => i.href));
    expect(hrefs).toContain("/sessions");
    expect(hrefs).toContain("/memory/learning");
    expect(hrefs).toContain("/memory/graph");
    expect(activeNavHref("/memory/graph")).toBe("/memory/graph");
  });

  it("marks only the most specific matching item", () => {
    expect(activeNavHref("/memory/learning")).toBe("/memory/learning");
    expect(activeNavHref("/memory")).toBe("/memory");
    expect(activeNavHref("/memory/ingestion")).toBe("/memory");
    expect(activeNavHref("/harness/jobs/1")).toBe("/harness");
    expect(activeNavHref("/nowhere")).toBeNull();
  });
});
