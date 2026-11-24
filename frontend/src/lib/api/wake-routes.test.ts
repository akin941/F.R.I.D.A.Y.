import { describe, expect, it } from "vitest";
import { routeLabel, type WakeRoute } from "./wake-routes";

const route = (over: Partial<WakeRoute>): WakeRoute => ({
  name: "digest.today",
  effect: "read",
  phrases: [],
  aliases: [],
  ...over,
});

describe("routeLabel", () => {
  it("keeps Friday's words for the code-defined routes", () => {
    expect(routeLabel(route({})).title).toBe("Bugünün brifingi");
  });

  it("titles a tool route with its tool label and says a write only proposes", () => {
    const write = routeLabel(
      route({ name: "tool.mcp_n8n_home_backup", effect: "proposal", label: "Ev yedeği" }),
    );
    expect(write.title).toBe("Ev yedeği");
    expect(write.description).toContain("taslak kart");
    const read = routeLabel(route({ name: "tool.n8n_server_status", label: "" }));
    expect(read.title).toBe("n8n_server_status");
    expect(read.description).toContain("okur");
  });

  it("falls back to the raw name for anything else", () => {
    expect(routeLabel(route({ name: "x.unknown" }))).toEqual({ title: "x.unknown", description: "" });
  });
});
