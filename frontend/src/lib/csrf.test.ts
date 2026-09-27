import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { rejectCrossOrigin, rejectNonJson } from "@/lib/csrf";

function makeRequest(headers: Record<string, string>) {
  return new NextRequest("http://localhost:3001/api/auth/enroll", {
    method: "POST",
    headers,
  });
}

describe("rejectCrossOrigin", () => {
  it("allows requests whose Origin matches APP_ORIGIN", () => {
    const request = makeRequest({ origin: "http://localhost:3001" });
    expect(rejectCrossOrigin(request)).toBeNull();
  });

  it("rejects a mismatched Origin with 403", async () => {
    const request = makeRequest({ origin: "https://evil.example" });
    const response = rejectCrossOrigin(request);
    expect(response?.status).toBe(403);
  });

  it("rejects a missing Origin header with 403", () => {
    const request = makeRequest({});
    const response = rejectCrossOrigin(request);
    expect(response?.status).toBe(403);
  });
});

describe("rejectNonJson", () => {
  it("allows application/json content type", () => {
    const request = makeRequest({ "content-type": "application/json" });
    expect(rejectNonJson(request)).toBeNull();
  });

  it("rejects a non-JSON content type with 403", () => {
    const request = makeRequest({ "content-type": "text/plain" });
    const response = rejectNonJson(request);
    expect(response?.status).toBe(403);
  });

  it("rejects a media type that merely contains 'application/json' as a substring", () => {
    // Regression test: the original implementation used
    // `contentType.includes("application/json")`, which "application/json-lines"
    // would have satisfied even though it is a different media type.
    const request = makeRequest({ "content-type": "application/json-lines" });
    const response = rejectNonJson(request);
    expect(response?.status).toBe(403);
  });

  it("allows application/json with a charset parameter", () => {
    const request = makeRequest({ "content-type": "application/json; charset=utf-8" });
    expect(rejectNonJson(request)).toBeNull();
  });
});
