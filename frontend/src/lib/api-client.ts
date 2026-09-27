"use client";

import { isSpecificError, messageForError, resolveErrorCode } from "@/lib/i18n/error-codes";
import { tr } from "@/lib/i18n/tr";
import type { ApiErrorBody } from "@/types/api";

/**
 * A non-2xx API response. `message` is the Turkish catalog message for the
 * body's stable `code` (backend core/error_codes.py), resolved by status
 * when the body has none. The backend's English `detail` is kept only as
 * `detail`, for ErrorState's technical-details disclosure -- never render
 * it anywhere else.
 */
export class ApiError extends Error {
  status: number;
  body: ApiErrorBody;
  code: string;
  detail: string | null;

  constructor(status: number, body: ApiErrorBody) {
    super(messageForError(body.code, status));
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.code = resolveErrorCode(body.code, status);
    this.detail = typeof body.detail === "string" ? body.detail : null;
  }

  /** True when `message` says more than a caller's own contextual text. */
  get specific(): boolean {
    return isSpecificError(this.body.code, this.status);
  }
}

/**
 * The Turkish text to show for a failed call: the catalog message for a
 * specific error code (permission, expiry, throttling, a domain rule),
 * otherwise the caller's `fallback` ("Görev oluşturulamadı."), and the
 * generic message for a non-HTTP failure (network, parse).
 */
export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.specific ? err.message : fallback;
  return tr.errors.generic;
}

let refreshPromise: Promise<boolean> | null = null;

function refreshSession(): Promise<boolean> {
  // Concurrent 401s must not each trigger their own refresh call — that would
  // race two rotations against the same refresh token and strand one caller.
  if (!refreshPromise) {
    refreshPromise = fetch("/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
      .then((res) => res.ok)
      .catch(() => false)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

/**
 * Fetch wrapper for same-origin BFF routes. On a single 401 it attempts one
 * refresh and retries the original request exactly once; a second 401 (or a
 * failed refresh) is treated as a hard logout, never as a retry loop.
 */
export async function apiFetch(input: string, init: RequestInit = {}, isRetry = false): Promise<Response> {
  const response = await fetch(input, { ...init, credentials: "same-origin" });
  if (response.status !== 401) {
    return response;
  }
  if (isRetry) {
    // Refresh succeeded but the retried request is 401 again (e.g. the
    // session was revoked mid-flight) — this is not a transient failure to
    // retry further, it's a hard logout.
    if (typeof window !== "undefined") {
      // A full-page navigation is deliberate: it drops every in-memory
      // view state along with the dead session (not a client-side push).
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = "/login";
    }
    return response;
  }
  const refreshed = await refreshSession();
  if (!refreshed) {
    if (typeof window !== "undefined") {
      // A full-page navigation is deliberate: it drops every in-memory
      // view state along with the dead session (not a client-side push).
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = "/login";
    }
    return response;
  }
  return apiFetch(input, init, true);
}

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}) as ApiErrorBody)) as ApiErrorBody;
    throw new ApiError(response.status, body);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export async function apiGetJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, { ...init, method: "GET" });
  return parseJsonOrThrow<T>(response);
}

export async function apiPostJson<T>(path: string, body?: unknown, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, {
    ...init,
    method: "POST",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return parseJsonOrThrow<T>(response);
}

/**
 * For multipart uploads (knowledge file-upload) — the browser computes the
 * `Content-Type: multipart/form-data; boundary=...` header itself from a
 * `FormData` body, so unlike `apiPostJson` this must NOT set one manually
 * (a fixed header would carry the wrong boundary).
 */
export async function apiPostForm<T>(path: string, formData: FormData, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, {
    ...init,
    method: "POST",
    body: formData,
  });
  return parseJsonOrThrow<T>(response);
}

export async function apiPatchJson<T>(path: string, body: unknown, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, {
    ...init,
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    body: JSON.stringify(body),
  });
  return parseJsonOrThrow<T>(response);
}

export async function apiDelete(path: string, init?: RequestInit): Promise<void> {
  const response = await apiFetch(path, {
    ...init,
    method: "DELETE",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  await parseJsonOrThrow<undefined>(response);
}
