/**
 * A same-tab signal that the session ended (logout, or a hard 401 after a
 * failed refresh / revoked device). Listeners that hold something open for
 * the signed-in owner -- wake mode's microphone (Phase 6 branch 4, D6) --
 * close it right away instead of waiting for the navigation to finish.
 */
export const AUTH_LOST_EVENT = "friday:auth-lost";

export function signalAuthLost(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(AUTH_LOST_EVENT));
}
