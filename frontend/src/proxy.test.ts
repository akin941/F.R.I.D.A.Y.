import { readdirSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { config, proxy, SHELL_PATHS } from "@/proxy";

function requestWithCookie(pathname: string, hasSession: boolean) {
  const request = new NextRequest(`http://localhost:3001${pathname}`);
  if (hasSession) {
    request.cookies.set("friday_session", "1");
  }
  return request;
}

const SHELL_DIR = path.resolve(process.cwd(), "src/app/(shell)");

function shellRoutes() {
  return readdirSync(SHELL_DIR)
    .filter((entry) => statSync(path.join(SHELL_DIR, entry)).isDirectory())
    .map((entry) => `/${entry}`)
    .sort();
}

describe("proxy", () => {
  it("guards every route of the (shell) group", () => {
    expect([...SHELL_PATHS].sort()).toEqual(shellRoutes());
    for (const route of shellRoutes()) {
      expect(config.matcher).toContain(`${route}/:path*`);
    }
  });

  it.each(SHELL_PATHS)(
    "sends a visitor without a session from %s to /enroll",
    (route) => {
      const response = proxy(requestWithCookie(route, false));
      expect(response.headers.get("location")).toBe(
        "http://localhost:3001/enroll",
      );
    },
  );

  it("guards nested shell paths too", () => {
    const response = proxy(requestWithCookie("/harness/jobs/123", false));
    expect(response.headers.get("location")).toBe(
      "http://localhost:3001/enroll",
    );
  });

  it("lets a visitor with a session through to a shell route", () => {
    const response = proxy(requestWithCookie("/chat", true));
    expect(response.headers.get("location")).toBeNull();
  });

  it("accepts the pre-rename jarvis_session cookie (Jarvis -> Friday)", () => {
    const request = new NextRequest("http://localhost:3001/chat");
    request.cookies.set("jarvis_session", "1");
    expect(proxy(request).headers.get("location")).toBeNull();
  });

  it("sends a visitor with a session away from /enroll", () => {
    const response = proxy(requestWithCookie("/enroll", true));
    expect(response.headers.get("location")).toBe("http://localhost:3001/chat");
  });

  it("lets a visitor without a session reach /enroll", () => {
    const response = proxy(requestWithCookie("/enroll", false));
    expect(response.headers.get("location")).toBeNull();
  });

  it("has no login or signup route left", () => {
    expect(config.matcher).not.toContain("/login");
    expect(config.matcher).not.toContain("/signup");
  });
});
