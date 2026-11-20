"use client";

import { useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { PatKind } from "@/types/api";

/** The OpenAI-compatible facade's public base URL (Phase 4 branch 6). */
export function facadeBaseUrl(origin: string) {
  return `${origin.replace(/\/$/, "")}/v1`;
}

/** Copyable Open WebUI connection settings for an `openai` token. */
export function openWebUiSnippet(origin: string, token: string) {
  return [
    "Open WebUI → Yönetici Paneli → Ayarlar → Bağlantılar → OpenAI API",
    `API Base URL: ${facadeBaseUrl(origin)}`,
    `API Key: ${token}`,
    "Model: friday (sohbet)",
    "Görev modeli (başlık, etiket, öneriler): friday-lite",
  ].join("\n");
}

function useCopy() {
  const [copied, setCopied] = useState<string | null>(null);
  async function copy(key: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(key);
    } catch {
      setCopied(null);
    }
  }
  return { copied, copy };
}

/**
 * The raw token value only ever appears in the create response body
 * (accounts/views.py:131) -- never re-fetchable, never re-displayed. This
 * banner is the one place it's ever rendered, and it disappears the moment
 * the caller dismisses it or creates another token, never persisted to
 * component state anywhere else. An `openai` token also gets its Open WebUI
 * setup snippet (base URL + key + models), copyable in one go.
 */
export function RevealedTokenBanner({
  token,
  kind = "cli",
  onDismiss,
}: {
  token: string;
  kind?: PatKind;
  onDismiss: () => void;
}) {
  const { copied, copy } = useCopy();
  const snippet =
    kind === "openai" && typeof window !== "undefined"
      ? openWebUiSnippet(window.location.origin, token)
      : null;

  return (
    <Alert>
      <AlertTitle>Bu token&apos;ı şimdi kopyala — bir daha gösterilmeyecek</AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <code className="break-all rounded-md bg-muted px-2 py-1 text-xs">{token}</code>
        {snippet ? (
          <>
            <p className="text-sm">
              Open WebUI kurulumu: bağlantı ayarlarına bu adresi ve anahtarı gir. Sohbet için{" "}
              <code>friday</code>, başlık ve etiket gibi arka plan görevleri için görev modeli olarak{" "}
              <code>friday-lite</code> seç.
            </p>
            <pre
              aria-label="Open WebUI kurulum bilgisi"
              className="whitespace-pre-wrap break-all rounded-md bg-muted px-2 py-1 text-xs"
            >
              {snippet}
            </pre>
          </>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => void copy("token", token)}>
            {copied === "token" ? "Kopyalandı" : "Kopyala"}
          </Button>
          {snippet ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => void copy("snippet", snippet)}
            >
              {copied === "snippet" ? "Kurulum bilgisi kopyalandı" : "Kurulum bilgisini kopyala"}
            </Button>
          ) : null}
          <Button type="button" size="sm" onClick={onDismiss}>
            Tamam
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
