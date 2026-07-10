import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "@/proxy";

function requestWithCookie(path: string, hasSession: boolean) {
  const request = new NextRequest(`http://localhost:3001${path}`);
  if (hasSession) {
    request.cookies.set("jarvis_session", "1");
  }
  return request;
}

describe("proxy", () => {
  it("redirects an unauthenticated visitor away from a protected path", () => {
    const response = proxy(requestWithCookie("/chat", false));
    expect(response.headers.get("location")).toBe("http://localhost:3001/login");
  });

  it("lets an authenticated visitor through to a protected path", () => {
    const response = proxy(requestWithCookie("/chat", true));
    expect(response.headers.get("location")).toBeNull();
  });

  it("redirects an authenticated visitor away from the login page", () => {
    const response = proxy(requestWithCookie("/login", true));
    expect(response.headers.get("location")).toBe("http://localhost:3001/chat");
  });

  it("lets an unauthenticated visitor through to the login page", () => {
    const response = proxy(requestWithCookie("/login", false));
    expect(response.headers.get("location")).toBeNull();
  });

  it("lets an unauthenticated visitor through to the signup page", () => {
    const response = proxy(requestWithCookie("/signup", false));
    expect(response.headers.get("location")).toBeNull();
  });
});
