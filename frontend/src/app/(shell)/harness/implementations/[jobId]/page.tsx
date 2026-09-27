import { ImplementationJobDetailPage } from "@/components/harness/ImplementationJobDetail";

export default async function ImplementationJobPage({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = await params;
  return <ImplementationJobDetailPage jobId={jobId} />;
}
