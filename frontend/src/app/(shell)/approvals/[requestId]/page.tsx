import { ApprovalDetail } from "@/components/approvals/ApprovalDetail";

export default async function ApprovalDetailPage({
  params,
}: {
  params: Promise<{ requestId: string }>;
}) {
  const { requestId } = await params;
  return <ApprovalDetail requestId={requestId} />;
}
