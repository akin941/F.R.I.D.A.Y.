import { ERROR_MESSAGES, type ErrorCode } from "@/lib/i18n/tr";

// Mirrors backend core/api_errors.py STATUS_FALLBACK_CODES: a body with no
// usable `code` (a BFF-built error, a proxy page) is read by its status.
const STATUS_CODES: Record<number, ErrorCode> = {
  400: "invalid",
  401: "not_authenticated",
  403: "permission_denied",
  404: "not_found",
  405: "method_not_allowed",
  406: "not_acceptable",
  409: "conflict",
  415: "unsupported_media_type",
  429: "throttled",
  503: "service_unavailable",
};

export function isKnownErrorCode(code: unknown): code is ErrorCode {
  return typeof code === "string" && Object.hasOwn(ERROR_MESSAGES, code);
}

/** The catalog code for a response: its own `code` when known, else by status. */
export function resolveErrorCode(code: unknown, status: number): ErrorCode {
  if (isKnownErrorCode(code)) return code;
  return STATUS_CODES[status] ?? "error";
}

/** Turkish message for a response. Never the backend's English `detail`. */
export function messageForError(code: unknown, status: number): string {
  return ERROR_MESSAGES[resolveErrorCode(code, status)].message;
}

/** Whether the resolved message says more than a caller's own context would. */
export function isSpecificError(code: unknown, status: number): boolean {
  return ERROR_MESSAGES[resolveErrorCode(code, status)].specific;
}
