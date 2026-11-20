"use client";

import { useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useWorkspace } from "@/contexts/workspace-context";

const DISMISSED_KEY = "friday:dismissed-security-notices";

function readDismissed(): string[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(window.localStorage.getItem(DISMISSED_KEY) ?? "[]") as string[];
  } catch {
    return [];
  }
}

/**
 * "New device enrolled remotely" (Phase 4 branch 2): every remote-key
 * enrollment of the last week is surfaced on every device until dismissed
 * there -- a device the owner does not recognize is revoked from
 * Entegrasyonlar → Cihazlar.
 */
export function SecurityNoticeBanner() {
  const { securityNotices } = useWorkspace();
  const [dismissed, setDismissed] = useState<string[]>(readDismissed);
  const visible = securityNotices.filter((notice) => !dismissed.includes(notice.device_id));
  if (visible.length === 0) return null;

  function dismiss(deviceId: string) {
    const next = [...dismissed, deviceId];
    setDismissed(next);
    try {
      window.localStorage.setItem(DISMISSED_KEY, JSON.stringify(next));
    } catch {
      // storage unavailable: dismissal lasts for this page only
    }
  }

  return (
    <div className="flex flex-col gap-2 p-3">
      {visible.map((notice) => (
        <Alert key={notice.device_id} role="status">
          <AlertTitle>Uzaktan yeni cihaz eşleştirildi</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center gap-2">
            <span>
              &quot;{notice.device_name}&quot; — {new Date(notice.created_at).toLocaleString()}.
              Tanımıyorsan Entegrasyonlar → Cihazlar&apos;dan iptal et.
            </span>
            <Button size="sm" variant="outline" onClick={() => dismiss(notice.device_id)}>
              Tamam
            </Button>
          </AlertDescription>
        </Alert>
      ))}
    </div>
  );
}
