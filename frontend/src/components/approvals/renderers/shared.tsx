import { tr } from "@/lib/i18n/tr";

export function SummaryRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
      <dt className="shrink-0 text-muted-foreground sm:w-32">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

/** Shown when a tool with a `describe_request` hook has no summary for this viewer/row. */
export function NoSummary() {
  return <p className="text-sm text-muted-foreground">{tr.approvals.noSummary}</p>;
}

export function toolTitle(toolName: string): string {
  return tr.approvals.toolTitles[toolName] ?? toolName;
}
