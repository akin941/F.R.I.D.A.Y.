import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/** A calm "nothing here yet" block: optional icon, a line, an optional action. */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      data-slot="empty-state"
      className={cn(
        "flex flex-col items-center gap-2 rounded-lg border border-dashed px-6 py-10 text-center",
        className,
      )}
    >
      {Icon ? <Icon aria-hidden className="size-6 text-muted-foreground" /> : null}
      <p className="text-sm font-medium">{title}</p>
      {description ? <p className="max-w-prose text-sm text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
