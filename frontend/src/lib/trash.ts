/** Backend default of TRASH_RETENTION_DAYS (config/settings/base.py, ADR-009). */
export const TRASH_RETENTION_DAYS_DEFAULT = 30;

/** When a row trashed at `deletedAt` is purged by the retention sweep. */
export function purgeDate(deletedAt: string, retentionDays: number): Date {
  return new Date(new Date(deletedAt).getTime() + retentionDays * 24 * 60 * 60 * 1000);
}
