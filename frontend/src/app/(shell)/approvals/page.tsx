import { redirect } from "next/navigation";
import { ApprovalInbox } from "@/components/approvals/ApprovalInbox";
import { approvalHref } from "@/components/approvals/links";
import { isValidUuid } from "@/lib/uuid";

// `/approvals?request=<id>` (the pre-routing deep link from harness gate
// chips) redirects to the routed detail `/approvals/[requestId]`.
export default async function ApprovalsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { request } = await searchParams;
  if (typeof request === "string" && isValidUuid(request)) {
    redirect(approvalHref(request));
  }
  return <ApprovalInbox />;
}
