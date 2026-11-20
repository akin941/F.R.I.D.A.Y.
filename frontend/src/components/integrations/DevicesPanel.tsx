"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { apiGetJson, apiPostJson, ApiError } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import type {
  ApiDeviceEnrolledVia,
  ApiPairingCode,
  ApiRemoteKeyState,
  ApiTrustedDevice,
} from "@/types/api";

const VIA_LABELS: Record<ApiDeviceEnrolledVia, string> = {
  pin: "PIN ile (yerel ağ)",
  strong_code: "Güçlü kod ile",
  remote_key: "Uzaktan erişim anahtarı ile",
};

const keyCopy = tr.confirm.remoteKey;

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

function errorText(err: unknown, fallback: string) {
  return err instanceof ApiError ? (err.body.detail ?? fallback) : fallback;
}

/** A pairing code or remote key: shown exactly once, never re-fetchable. */
function OneTimeSecret({
  title,
  value,
  hint,
  onDismiss,
}: {
  title: string;
  value: string;
  hint: string;
  onDismiss: () => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <Alert>
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <code className="break-all rounded-md bg-muted px-2 py-1 text-base tracking-wider">
          {value}
        </code>
        <span className="text-xs text-muted-foreground">{hint}</span>
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => {
              navigator.clipboard
                .writeText(value)
                .then(() => setCopied(true))
                .catch(() => setCopied(false));
            }}
          >
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

function DeviceRow({
  device,
  onChanged,
  onForgotten,
}: {
  device: ApiTrustedDevice;
  onChanged: (device: ApiTrustedDevice) => void;
  onForgotten: () => void;
}) {
  const copy = tr.confirm.device;
  const isNode = device.kind === "node";

  // Both run only from their confirmation dialog, which reports a failure.
  async function handleRevoke() {
    onChanged(await apiPostJson<ApiTrustedDevice>(`/api/auth/devices/${device.id}/revoke`));
  }

  async function handleForget() {
    await apiPostJson("/api/auth/forget");
    onForgotten();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {device.name}
          {isNode ? (
            <span className="rounded-md border px-2 py-0.5 text-xs font-medium">
              {copy.nodeBadge}
            </span>
          ) : null}
          {device.is_current ? (
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium">Bu cihaz</span>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{VIA_LABELS[device.enrolled_via]}</span>
          <span>&middot;</span>
          <span>Eşleştirildi {formatDate(device.created_at)}</span>
          <span>&middot;</span>
          <span>Son görülme {formatDate(device.last_seen_at)}</span>
          {device.last_seen_ip ? (
            <>
              <span>&middot;</span>
              <span>{device.last_seen_ip}</span>
            </>
          ) : null}
        </div>
        {device.revoked_at ? (
          <p className="text-sm text-muted-foreground">
            İptal edildi {formatDate(device.revoked_at)}
          </p>
        ) : device.is_current && !isNode ? (
          <div>
            <ConfirmDialog
              trigger={
                <Button variant="outline" size="sm">
                  Bu cihazı unut
                </Button>
              }
              title={copy.forgetTitle}
              description={copy.forget}
              confirmLabel={copy.forgetConfirm}
              busyLabel={copy.forgetting}
              errorFallback={copy.forgetFailed}
              onConfirm={handleForget}
            />
          </div>
        ) : (
          <div>
            <ConfirmDialog
              trigger={
                <Button variant="outline" size="sm">
                  İptal et
                </Button>
              }
              title={isNode ? copy.revokeNodeTitle : copy.revokeTitle}
              description={isNode ? copy.revokeNode(device.name) : copy.revoke(device.name)}
              confirmLabel={isNode ? copy.revokeNodeConfirm : copy.revokeConfirm}
              busyLabel={copy.revoking}
              errorFallback={copy.revokeFailed}
              onConfirm={handleRevoke}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Trusted devices (Phase 4 branch 2): the list with revoke / "Bu cihazı
 * unut", "Yeni cihaz eşleştir" (a PIN, or a strong code for another
 * network) and the remote access key. Friday Nodes (Phase 9 D1, the Mac
 * app) are listed with a "Mac düğümü" badge and their own revoke wording;
 * node pairing and the kill switch toggle arrive with the /devices node
 * section (Phase 9 branch 7).
 */
export function DevicesPanel() {
  const [devices, setDevices] = useState<ApiTrustedDevice[]>([]);
  const [remoteKey, setRemoteKey] = useState<ApiRemoteKeyState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pairingCode, setPairingCode] = useState<ApiPairingCode | null>(null);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, key] = await Promise.all([
        apiGetJson<ApiTrustedDevice[]>("/api/auth/devices"),
        apiGetJson<ApiRemoteKeyState>("/api/auth/remote-key"),
      ]);
      setDevices(list);
      setRemoteKey(key);
    } catch {
      setError("Cihazlar yüklenemedi.");
    } finally {
      setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount, the standard data-fetching effect pattern */
  useEffect(() => {
    void load();
  }, [load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function issueCode(kind: "pin" | "strong") {
    setBusy(true);
    setError(null);
    try {
      setPairingCode(await apiPostJson<ApiPairingCode>("/api/auth/pairing-codes", { kind }));
    } catch (err) {
      setError(errorText(err, "Eşleştirme kodu üretilemedi."));
    } finally {
      setBusy(false);
    }
  }

  // Both run only from their confirmation dialog, which reports a failure.
  async function rotateKey() {
    const { key, ...state } = await apiPostJson<ApiRemoteKeyState>("/api/auth/remote-key");
    setRemoteKey(state);
    setRevealedKey(key ?? null);
  }

  async function revokeKey() {
    setRemoteKey(await apiPostJson<ApiRemoteKeyState>("/api/auth/remote-key/revoke"));
    setRevealedKey(null);
  }

  function handleForgotten() {
    // A full navigation: this browser is no longer trusted at all.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/enroll";
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void issueCode("pin")} disabled={busy}>
          Yeni cihaz eşleştir
        </Button>
        <Button variant="outline" onClick={() => void issueCode("strong")} disabled={busy}>
          Ağ dışı için güçlü kod
        </Button>
      </div>

      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {pairingCode ? (
        <OneTimeSecret
          key={pairingCode.code}
          title={pairingCode.kind === "pin" ? "Eşleştirme PIN'i" : "Güçlü eşleştirme kodu"}
          value={pairingCode.code}
          hint={`Yeni cihazda /enroll sayfasına gir. Tek kullanımlık; ${formatDate(
            pairingCode.expires_at,
          )} tarihine kadar geçerli.${pairingCode.kind === "pin" ? " Yalnızca yerel ağdan." : ""}`}
          onDismiss={() => setPairingCode(null)}
        />
      ) : null}

      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {devices.map((device) => (
            <DeviceRow
              key={device.id}
              device={device}
              onChanged={(updated) =>
                setDevices((prev) => prev.map((d) => (d.id === updated.id ? updated : d)))
              }
              onForgotten={handleForgotten}
            />
          ))}
        </div>
      )}

      <div className="flex flex-col gap-2 pt-2">
        <h3 className="text-sm font-medium">Uzaktan erişim anahtarı</h3>
        <p className="text-sm text-muted-foreground">
          {remoteKey?.active
            ? `Açık — oluşturuldu ${formatDate(remoteKey.created_at)}, son kullanım ${formatDate(
                remoteKey.last_used_at,
              )}. Her kullanım tüm cihazlarda bildirilir.`
            : "Kapalı. Açarsan, anahtarı bilen biri herhangi bir ağdan yeni cihaz eşleştirebilir."}
        </p>
        {revealedKey ? (
          <OneTimeSecret
            key={revealedKey}
            title="Uzaktan erişim anahtarı — şimdi kopyala, bir daha gösterilmeyecek"
            value={revealedKey}
            hint="Yenileyene ya da kapatana kadar geçerli."
            onDismiss={() => setRevealedKey(null)}
          />
        ) : null}
        <div className="flex flex-wrap gap-2">
          <ConfirmDialog
            trigger={
              <Button variant="outline" size="sm" disabled={busy}>
                {remoteKey?.active ? "Anahtarı yenile" : "Anahtar oluştur"}
              </Button>
            }
            title={remoteKey?.active ? keyCopy.rotateTitle : keyCopy.createTitle}
            description={remoteKey?.active ? keyCopy.rotate : keyCopy.create}
            confirmLabel={remoteKey?.active ? keyCopy.rotateConfirm : keyCopy.createConfirm}
            busyLabel={keyCopy.busy}
            destructive={Boolean(remoteKey?.active)}
            errorFallback={keyCopy.createFailed}
            onConfirm={rotateKey}
          />
          {remoteKey?.active ? (
            <ConfirmDialog
              trigger={
                <Button variant="outline" size="sm" disabled={busy}>
                  Kapat
                </Button>
              }
              title={keyCopy.revokeTitle}
              description={keyCopy.revoke}
              confirmLabel={keyCopy.revokeConfirm}
              busyLabel={keyCopy.busy}
              errorFallback={keyCopy.revokeFailed}
              onConfirm={revokeKey}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
