"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";

// Plain fetch, not apiFetch: this page is where apiFetch sends a dead
// session, so it must never trigger apiFetch's refresh-then-redirect loop.
async function postJson(path: string, body?: unknown) {
  return fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function guessDeviceName(userAgent: string): string {
  const os = /iPhone/.test(userAgent)
    ? "iPhone"
    : /iPad/.test(userAgent)
      ? "iPad"
      : /Android/.test(userAgent)
        ? "Android"
        : /Mac OS X|Macintosh/.test(userAgent)
          ? "Mac"
          : /Windows/.test(userAgent)
            ? "Windows"
            : /Linux/.test(userAgent)
              ? "Linux"
              : "Cihaz";
  const browser = /Edg\//.test(userAgent)
    ? "Edge"
    : /Firefox\//.test(userAgent)
      ? "Firefox"
      : /Chrome\//.test(userAgent)
        ? "Chrome"
        : /Safari\//.test(userAgent)
          ? "Safari"
          : "";
  return browser ? `${os} — ${browser}` : os;
}

function errorMessage(status: number, code: string | undefined): string {
  if (status === 429) return "Çok fazla deneme. Biraz bekleyip tekrar dene.";
  if (code === "pin_network_refused") {
    return "PIN kodları yalnızca yerel ağdan kabul edilir. Güçlü bir kod ya da uzaktan erişim anahtarı kullan.";
  }
  if (code === "invalid_code") return "Kod geçersiz, kullanılmış ya da süresi dolmuş.";
  return "Cihaz eşleştirilemedi. Lütfen tekrar dene.";
}

/**
 * Device enrollment (Phase 4 branch 2) -- replaces signup and password login.
 * On mount it first tries to resume a session from this browser's
 * trusted-device cookie; only a browser that is not (or no longer) trusted
 * sees the form. After an explicit "Çıkış yap" (`?signed_out=1`) it does not
 * resume on its own -- the owner chooses to continue with this device.
 */
export function EnrollForm() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [code, setCode] = useState("");
  const [deviceName, setDeviceName] = useState(() =>
    typeof navigator === "undefined" ? "" : guessDeviceName(navigator.userAgent),
  );
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [offerResume, setOfferResume] = useState(false);

  async function resume() {
    setSubmitting(true);
    setFormError(null);
    try {
      const response = await postJson("/api/auth/session/start");
      if (response.ok) {
        router.replace("/chat");
        router.refresh();
        return;
      }
      setOfferResume(false);
      setFormError("Bu tarayıcı artık güvenilir bir cihaz değil. Yeni bir kodla eşleştir.");
    } catch {
      setFormError("Bir şeyler ters gitti. Lütfen tekrar dene.");
    } finally {
      setSubmitting(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    if (new URLSearchParams(window.location.search).has("signed_out")) {
      /* eslint-disable react-hooks/set-state-in-effect -- one-time read of the URL on mount */
      setOfferResume(true);
      setChecking(false);
      /* eslint-enable react-hooks/set-state-in-effect */
      return;
    }
    postJson("/api/auth/session/start")
      .then((response) => {
        if (cancelled) return;
        if (response.ok) {
          router.replace("/chat");
          router.refresh();
          return;
        }
        setChecking(false);
      })
      .catch(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setFormError(null);
    try {
      const response = await postJson("/api/auth/enroll", {
        code: code.trim(),
        device_name: deviceName.trim(),
      });
      if (response.ok) {
        router.push("/chat");
        router.refresh();
        return;
      }
      const body = (await response.json().catch(() => ({}))) as { code?: string };
      setFormError(errorMessage(response.status, body.code));
    } catch {
      setFormError("Bir şeyler ters gitti. Lütfen tekrar dene.");
    } finally {
      setSubmitting(false);
    }
  }

  if (checking) {
    return (
      <Card className="w-full max-w-sm" aria-busy="true">
        <CardContent className="flex flex-col gap-3 pt-6">
          <Skeleton className="h-6 w-2/3" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>Bu cihazı eşleştir</CardTitle>
      </CardHeader>
      <CardContent>
        {offerResume ? (
          <div className="mb-4 flex flex-col gap-2">
            <p className="text-sm text-muted-foreground">Oturumu kapattın.</p>
            <Button type="button" onClick={() => void resume()} disabled={submitting}>
              Bu cihazla devam et
            </Button>
          </div>
        ) : null}
        <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
          {formError ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          ) : null}
          <div className="flex flex-col gap-2">
            <Label htmlFor="pairing-code">Eşleştirme kodu</Label>
            <Input
              id="pairing-code"
              autoComplete="one-time-code"
              autoCapitalize="characters"
              spellCheck={false}
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="device-name">Cihaz adı</Label>
            <Input
              id="device-name"
              maxLength={100}
              required
              value={deviceName}
              onChange={(e) => setDeviceName(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={submitting}>
            {submitting ? "Eşleştiriliyor…" : "Cihazı eşleştir"}
          </Button>
          <p className="text-sm text-muted-foreground">
            İlk cihaz için sunucuda <code>manage.py issue_pairing_code</code> çalıştır. Başka
            cihazlar için güvenilir bir cihazda Entegrasyonlar → Cihazlar → &quot;Yeni cihaz
            eşleştir&quot;. Ağ dışından: güçlü kod ya da uzaktan erişim anahtarı (<code>jrk_…</code>).
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
