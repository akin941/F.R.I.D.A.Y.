import { Lock, Radio } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { tr } from "@/lib/i18n/tr";
import { TONE_PILL_CLASSES } from "@/lib/theme/status";
import { cn } from "@/lib/utils";

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
