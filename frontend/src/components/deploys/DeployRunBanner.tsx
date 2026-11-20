import { CircleAlert, CircleCheck, CircleHelp, Info, TriangleAlert, Undo2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { deployErrorLabel, isDeployActive, shortSha } from "@/components/deploys/labels";
import { tr } from "@/lib/i18n/tr";
import { statusTone, type StatusTone } from "@/lib/theme/status";
import { cn } from "@/lib/utils";

const copy = tr.deploys.banner;

const TONE_CLASSES: Record<StatusTone, string> = {
  success: "border-success/30 bg-success-muted text-success",
  warning: "border-warning/30 bg-warning-muted text-warning",
  info: "border-info/30 bg-info-muted text-info",
  danger: "border-danger/30 bg-danger-muted text-danger",
  neutral: "border-border bg-muted text-muted-foreground",
};

const ICONS: Record<string, LucideIcon> = {
  succeeded: CircleCheck,
  succeeded_degraded: TriangleAlert,
  rolled_back: Undo2,
  rollback_failed: CircleAlert,
  health_failed: CircleAlert,
  unknown_outcome: CircleHelp,
  failed: CircleAlert,
};

function message(status: string, rollbackSha: string): string | null {
  switch (status) {
    case "succeeded":
      return copy.succeeded;
    case "succeeded_degraded":
      return copy.succeeded_degraded;
    case "rolled_back":
      return copy.rolled_back(shortSha(rollbackSha, 7) || "—");
    case "rollback_failed":
      return copy.rollback_failed;
    case "health_failed":
      return copy.health_failed;
    case "unknown_outcome":
      return copy.unknown_outcome;
    case "failed":
      return copy.failed;
    case "refused":
      return copy.refused;
    case "cancelled":
      return copy.cancelled;
    default:
      return isDeployActive(status) ? copy.active : null;
  }
}

/**
 * A deploy run's outcome, one distinct banner per state (brief §9): the
 * five states that need the owner's attention -- `rolled_back`,
 * `rollback_failed`, `health_failed`, `succeeded_degraded`,
 * `unknown_outcome` -- each say what happened and what is live now, in
 * their own tone. A degraded deploy is a warning with its own wording and
 * never reuses the clean-success banner. The text is Friday's (closed
 * vocabulary), never remote output.
 */
export function DeployRunBanner({
  status,
  rollbackSha = "",
  errorCode = "",
  className,
}: {
  status: string;
  rollbackSha?: string;
  errorCode?: string;
  className?: string;
}) {
  const text = message(status, rollbackSha);
  if (!text) return null;
  const tone = statusTone("deployRun", status);
  const Icon = ICONS[status] ?? Info;
  const reason = deployErrorLabel(errorCode);
  const urgent = tone === "danger" || tone === "warning";
  return (
    <div
      role={urgent ? "alert" : "status"}
      data-testid={`deploy-banner-${status}`}
      data-tone={tone}
      className={cn(
        "flex items-start gap-2 rounded-lg border px-3 py-2 text-sm",
        TONE_CLASSES[tone],
        className,
      )}
    >
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="font-medium">{text}</p>
        {reason && status !== "succeeded" ? <p>{reason}</p> : null}
        {status === "pending" ? <p className="text-xs">{copy.runnerOffline}</p> : null}
      </div>
    </div>
  );
}
