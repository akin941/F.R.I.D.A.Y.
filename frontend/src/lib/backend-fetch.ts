import "server-only";
import { env } from "@/lib/env";

interface BackendFetchOptions {
  method?: string;
  body?: unknown;
  accessToken?: string;
  signal?: AbortSignal;
  accept?: string;
}

/**
 * Thin wrapper around the Django API. Server-side only — the browser never
 * sees BACKEND_API_URL or the bearer token this attaches.
 *
 * A `FormData` body (knowledge file-upload) is passed straight through
 * without a `Content-Type` header: `fetch` computes the multipart boundary
 * itself from the `FormData` instance, and setting the header manually here
 * would fix a boundary that doesn't match the one actually written.
 */
export async function backendFetch(path: string, options: BackendFetchOptions = {}) {
  const isFormData = options.body instanceof FormData;
  const headers: Record<string, string> = {
    Accept: options.accept ?? "application/json",
  };
  if (options.body !== undefined && !isFormData) {
    headers["Content-Type"] = "application/json";
  }
  if (options.accessToken) {
    headers.Authorization = `Bearer ${options.accessToken}`;
  }
  return fetch(`${env.BACKEND_API_URL}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: isFormData
      ? (options.body as FormData)
      : options.body !== undefined
        ? JSON.stringify(options.body)
        : undefined,
    signal: options.signal,
    cache: "no-store",
  });
}
