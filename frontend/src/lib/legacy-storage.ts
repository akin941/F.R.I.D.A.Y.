/**
 * Jarvis -> Friday rename (2026-11-20): localStorage keys moved from the
 * `jarvis:` prefix to `friday:`. Read the legacy key once when the new key
 * is absent, write it under the new name and drop the old one, so a theme
 * choice or a dismissal survives the upgrade. Never throws: blocked or
 * unavailable storage just means nothing is migrated.
 */
export function migrateLegacyStorageKey(newKey: string, legacyKey: string): void {
  try {
    if (typeof window === "undefined") return;
    const storage = window.localStorage;
    if (storage.getItem(newKey) !== null) return;
    const legacy = storage.getItem(legacyKey);
    if (legacy === null) return;
    storage.setItem(newKey, legacy);
    storage.removeItem(legacyKey);
  } catch {
    // Storage unavailable: the new key simply starts empty.
  }
}
