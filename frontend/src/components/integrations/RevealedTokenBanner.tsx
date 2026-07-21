"use client";

import { useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/**
 * The raw token value only ever appears in the create response body
 * (accounts/views.py:131) -- never re-fetchable, never re-displayed. This
 * banner is the one place it's ever rendered, and it disappears the moment
 * the caller dismisses it or creates another token, never persisted to
 * component state anywhere else.
 */
export function RevealedTokenBanner({ token, onDismiss }: { token: string; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Alert>
      <AlertTitle>Bu token&apos;ı şimdi kopyala — bir daha gösterilmeyecek</AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <code className="break-all rounded-md bg-muted px-2 py-1 text-xs">{token}</code>
        <div className="flex gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => void handleCopy()}>
            {copied ? "Kopyalandı" : "Kopyala"}
          </Button>
          <Button type="button" size="sm" onClick={onDismiss}>
            Tamam
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
