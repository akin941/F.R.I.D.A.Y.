import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { ApiError, errorMessage } from "@/lib/api-client";
import { messageForError, resolveErrorCode } from "@/lib/i18n/error-codes";
import { ERROR_MESSAGES, tr } from "@/lib/i18n/tr";

// The backend's closed error-code vocabulary. Parsed from source so a code
// added there without a Turkish message here fails CI.
const BACKEND_CODES_FILE = path.resolve(__dirname, "../../../../backend/core/error_codes.py");

function backendCodes(): string[] {
  const source = readFileSync(BACKEND_CODES_FILE, "utf8");
  return [...source.matchAll(/^\s+"([a-z_.]+)",\s*$/gm)].map((m) => m[1]!);
}

describe("error-code catalog", () => {
  it("has a Turkish message for every code the backend emits", () => {
    const codes = backendCodes();
    expect(codes.length).toBeGreaterThan(25);
    const missing = codes.filter((code) => !Object.hasOwn(ERROR_MESSAGES, code));
    expect(missing).toEqual([]);
  });

  it("has no entry the backend never emits", () => {
    const codes = new Set(backendCodes());
    const stale = Object.keys(ERROR_MESSAGES).filter((code) => !codes.has(code));
    expect(stale).toEqual([]);
  });

  it("resolves a known code, else falls back by status", () => {
    expect(resolveErrorCode("approval.expired", 400)).toBe("approval.expired");
    expect(resolveErrorCode("something.new", 403)).toBe("permission_denied");
    expect(resolveErrorCode(undefined, 429)).toBe("throttled");
    expect(resolveErrorCode(["field error"], 400)).toBe("invalid");
    expect(resolveErrorCode(undefined, 502)).toBe("error");
    expect(messageForError("approval.channel_forbidden", 403)).toBe(
      "Onay kararları yalnızca web uygulamasından verilebilir.",
    );
  });
});

describe("ApiError", () => {
  it("carries the Turkish message, never the English detail", () => {
    const err = new ApiError(400, { detail: "This request has expired.", code: "approval.expired" });
    expect(err.message).toBe("Bu onay isteğinin süresi doldu.");
    expect(err.code).toBe("approval.expired");
    expect(err.detail).toBe("This request has expired.");
  });

  it("errorMessage prefers a specific code and keeps the caller's context otherwise", () => {
    const specific = new ApiError(403, { detail: "Role does not allow x.", code: "task.permission_denied" });
    expect(errorMessage(specific, "Görev oluşturulamadı.")).toBe("Rolün bu görev işlemine yetmiyor.");

    const generic = new ApiError(400, { title: ["This field is required."], code: "invalid" });
    expect(errorMessage(generic, "Görev oluşturulamadı.")).toBe("Görev oluşturulamadı.");

    const bffBody = new ApiError(401, { detail: "Not authenticated." });
    expect(errorMessage(bffBody, "x")).toBe(ERROR_MESSAGES.not_authenticated.message);

    expect(errorMessage(new TypeError("Failed to fetch"), "x")).toBe(tr.errors.generic);
  });
});
