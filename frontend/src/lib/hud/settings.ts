/**
 * Per-device HUD settings (Phase 6 A8), browser storage only -- a
 * convenience, never trust-bearing. Defaults follow the battery rule: no
 * always-on resting animation on battery unless the owner turns it on;
 * earcons on (they are silent anyway while voice is muted).
 */

export interface HudSettings {
  animateOnBattery: boolean;
  earcons: boolean;
}

export const HUD_SETTINGS_KEY = "friday:hud:settings";
export const DEFAULT_HUD_SETTINGS: HudSettings = { animateOnBattery: false, earcons: true };

export function loadHudSettings(): HudSettings {
  try {
    const raw = window.localStorage.getItem(HUD_SETTINGS_KEY);
    if (!raw) return DEFAULT_HUD_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<HudSettings>;
    return {
      animateOnBattery: parsed.animateOnBattery === true,
      earcons: parsed.earcons !== false,
    };
  } catch {
    return DEFAULT_HUD_SETTINGS;
  }
}

export function saveHudSettings(next: Partial<HudSettings>): HudSettings {
  const merged = { ...loadHudSettings(), ...next };
  try {
    window.localStorage.setItem(HUD_SETTINGS_KEY, JSON.stringify(merged));
  } catch {
    // Per-device convenience only.
  }
  return merged;
}
