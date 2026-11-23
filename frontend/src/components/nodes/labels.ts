import { formatDateTime } from "@/lib/format/relative-time";
import { tr } from "@/lib/i18n/tr";
import type { ApiNodeCapability } from "@/types/api";

const copy = tr.nodes;

export function nodeStatusLabel(status: string): string {
  return copy.statuses[status] ?? status;
}

export function invocationStatusLabel(status: string): string {
  return copy.logStatuses[status] ?? status;
}

/**
 * A capability's availability as read-only text (D11): "Kullanılabilir",
 * or the `not_invocable` reason ("Mac'te izin verilmedi"). Policy is the
 * Mac's own (ADR-013 decision 5), so there is never a control here.
 */
export function capabilityAvailabilityText(entry: ApiNodeCapability): string {
  if (entry.invocable) return copy.capabilityInvocable;
  if (entry.reason) return copy.capabilityReasons[entry.reason] ?? copy.capabilityUnavailable;
  if (entry.availability === "not_granted") return copy.capabilityReasons.not_granted!;
  return copy.capabilityUnavailable;
}

export function formatWhen(value: string | null | undefined): string {
  return value ? formatDateTime(value) : copy.never;
}

/** A hash shortened for display (the full value stays in the title). */
export function shortHash(value: string): string {
  return value ? value.slice(0, 12) : "—";
}
