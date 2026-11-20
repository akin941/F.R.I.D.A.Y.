import { Lock, Plug, Radio, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { tr } from "@/lib/i18n/tr";
import { TONE_PILL_CLASSES } from "@/lib/theme/status";
import { cn } from "@/lib/utils";
import type { ApiConnectorRead } from "@/types/api";

/** The chat label for one connector read's source ("GitHub", "Posta", "Takvim", "Ev"). */
export function connectorSourceLabel(read: Pick<ApiConnectorRead, "source" | "tool">): string {
  const labels = tr.chat.sourceLabels;
  if (read.source === "google") {
    return (read.tool.startsWith("calendar") ? labels.google_calendar : labels.google_mail) ?? "";
  }
  return labels[read.source] ?? read.source;
}

/**
 * "Kaynak: GitHub" (Phase 5 branch 9): a reply whose context included a
 * connector read says which system it came from -- one badge per distinct
 * source, personal-class ones in the warning tone. Bookkeeping only
 * (`Message.connector_reads`), never the read's content.
 */
export function SourceBadges({
  reads,
  className,
}: {
  reads: ApiConnectorRead[] | undefined;
  className?: string;
}) {
  if (!reads || reads.length === 0) return null;
  const seen = new Map<string, boolean>();
  for (const read of reads) {
    const label = connectorSourceLabel(read);
    seen.set(label, (seen.get(label) ?? false) || read.data_class === "personal");
  }
  return (
    <>
      {[...seen.entries()].map(([label, personal]) => (
        <Badge
          key={label}
          variant="outline"
          data-testid="source-badge"
          data-personal={personal ? "true" : undefined}
          title={tr.chat.sourceHint}
          className={cn(personal ? TONE_PILL_CLASSES.warning : "text-muted-foreground", className)}
        >
          <Plug aria-hidden />
          {tr.chat.source(label)}
        </Badge>
      ))}
    </>
  );
}

export function PersonalDataBadge({ className }: { className?: string }) {
  return (
    <Badge
      variant="outline"
      data-testid="personal-data-badge"
      title={tr.chat.personalDataNotice}
      className={cn(TONE_PILL_CLASSES.warning, className)}
    >
      <ShieldAlert aria-hidden />
      {tr.chat.personalData}
    </Badge>
  );
}

/** Label for a message/conversation channel; an unknown value shows as-is. */
export function channelLabel(channel: string): string {
  return tr.chat.channel[channel] ?? channel;
}

/** Shown for turns that did NOT arrive over the web app (CLI, Telegram, ...). */
export function ChannelBadge({
  channel,
  className,
}: {
  channel: string | null | undefined;
  className?: string;
}) {
  if (!channel || channel === "web") return null;
  const label = channelLabel(channel);
  return (
    <Badge
      variant="outline"
      data-testid="channel-badge"
      title={tr.chat.viaChannel(label)}
      className={cn("text-muted-foreground", className)}
    >
      <Radio aria-hidden />
      {label}
    </Badge>
  );
}

export function PrivateBadge({ className }: { className?: string }) {
  return (
    <Badge
      variant="outline"
      data-testid="private-badge"
      title={tr.chat.privateHint}
      className={cn(TONE_PILL_CLASSES.warning, className)}
    >
      <Lock aria-hidden />
      {tr.chat.private}
    </Badge>
  );
}
