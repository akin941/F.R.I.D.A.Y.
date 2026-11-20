import "server-only";
import type { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { clientIpHeaders } from "@/lib/client-ip";
import {
  getDeviceSecret,
  migrateLegacyDeviceCookie,
  setAuthCookies,
} from "@/lib/auth-cookies";

export type DeviceSessionResult =
  | "ok"
  | "no_device"
  | "rejected"
  | "unavailable";

/**
 * Mint a fresh session from the trusted device's secret (Phase 4 branch 2):
 * `POST /auth/devices/session`. Used by `/api/auth/session/start` (a page
 * load without a session) and by `/api/auth/refresh` when the refresh token
 * itself is no longer accepted. Only "rejected" means the device is no
 * longer trusted -- the caller then drops the device cookie and the browser
 * re-enrolls; "unavailable" (backend down, throttled) must not.
 */
export async function remintFromDevice(
  request: NextRequest,
): Promise<DeviceSessionResult> {
  const deviceSecret = await getDeviceSecret();
  if (!deviceSecret) return "no_device";
  let response: Response;
  try {
    response = await backendFetch("/auth/devices/session", {
      method: "POST",
      body: { device_secret: deviceSecret },
      headers: clientIpHeaders(request),
    });
  } catch {
    return "unavailable";
  }
  if (response.status === 401) return "rejected";
  if (!response.ok) return "unavailable";
  const tokens = (await response.json()) as { access: string; refresh: string };
  await setAuthCookies({ access: tokens.access, refresh: tokens.refresh });
  await migrateLegacyDeviceCookie();
  return "ok";
}
