import { HarnessJobDetailPage } from "@/components/harness/HarnessJobDetail";

export default async function HarnessJobPage({ params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = await params;
  return <HarnessJobDetailPage jobId={jobId} />;
}
