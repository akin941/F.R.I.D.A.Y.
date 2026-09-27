import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  TONE_DOT_CLASSES,
  TONE_PILL_CLASSES,
  statusTone,
  type StatusDomain,
} from "@/lib/theme/status";

/**
 * A domain status rendered as a tinted pill in its semantic tone
 * (lib/theme/status.ts). The label is the caller's Turkish text -- the
 * color is decoration, the text carries the meaning (never color alone).
 */
export function StatusPill({
  domain,
  status,
  label,
  className,
}: {
  domain: StatusDomain;
  status: string;
  label: string;
  className?: string;
}) {
  const tone = statusTone(domain, status);
  return (
    <Badge
      variant="outline"
      data-tone={tone}
      data-status={status}
      className={cn("gap-1.5 font-medium", TONE_PILL_CLASSES[tone], className)}
    >
      <span aria-hidden className={cn("size-1.5 rounded-full", TONE_DOT_CLASSES[tone])} />
      {label}
    </Badge>
  );
}
