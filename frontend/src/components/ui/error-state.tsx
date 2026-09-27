"use client";

import { CircleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError, errorMessage } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { cn } from "@/lib/utils";

/**
 * A failed load or action: the Turkish message (catalog message for a
 * specific error code, else `fallback`), an optional retry, and -- for an
 * API error -- a collapsed "Teknik ayrıntılar" disclosure with the status,
 * the stable code and the backend's English `detail`. This disclosure is
 * the ONLY place a backend `detail` is ever rendered.
 */
export function ErrorState({
  error,
  fallback,
  onRetry,
  retrying = false,
  className,
}: {
  error: unknown;
  fallback: string;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}) {
  const apiError = error instanceof ApiError ? error : null;
  return (
    <div
      role="alert"
      data-slot="error-state"
      className={cn(
        "flex flex-col gap-2 rounded-lg border border-danger/30 bg-danger-muted px-3 py-2.5 text-sm",
        className,
      )}
    >
      <p className="flex items-start gap-2 text-danger">
        <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
        <span>{errorMessage(error, fallback)}</span>
      </p>
      {onRetry ? (
        <div>
          <Button variant="outline" size="sm" onClick={onRetry} disabled={retrying}>
            {tr.common.retry}
          </Button>
        </div>
      ) : null}
      {apiError ? (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">{tr.common.technicalDetails}</summary>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono">
            <dt>{tr.errors.status}</dt>
            <dd>{apiError.status}</dd>
            <dt>{tr.errors.code}</dt>
            <dd>{apiError.code}</dd>
            {apiError.detail ? (
              <>
                <dt>{tr.errors.detail}</dt>
                <dd className="break-words">{apiError.detail}</dd>
              </>
            ) : null}
          </dl>
        </details>
      ) : null}
    </div>
  );
}
