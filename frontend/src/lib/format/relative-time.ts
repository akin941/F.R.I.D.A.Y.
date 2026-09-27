import { tr } from "@/lib/i18n/tr";

/**
 * Turkish date/time formatting (`tr-TR`). Relative for recent moments
 * ("3 dakika önce", "dün", "2 gün sonra"), absolute for tooltips/titles.
 */

const LOCALE = "tr-TR";

const relative = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" });
const absolute = new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium", timeStyle: "short" });

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

function toDate(value: string | number | Date): Date {
  return value instanceof Date ? value : new Date(value);
}

/** "az önce", "5 dakika önce", "dün", "3 gün sonra" ... relative to `now`. */
export function formatRelativeTime(
  value: string | number | Date,
  now: Date = new Date(),
): string {
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return "";
  const seconds = Math.round((date.getTime() - now.getTime()) / 1000);
  if (Math.abs(seconds) < 45) return tr.time.justNow;
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return relative.format(Math.round(seconds / 60), "minute");
}

/** "27 Eyl 2026 14:05" -- the absolute form for titles and tooltips. */
export function formatDateTime(value: string | number | Date): string {
  const date = toDate(value);
  return Number.isNaN(date.getTime()) ? "" : absolute.format(date);
}
