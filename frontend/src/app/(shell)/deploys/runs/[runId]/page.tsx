import { DeployRunDetailPage } from "@/components/deploys/DeployRunDetail";

export default async function DeployRunPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;
  return <DeployRunDetailPage runId={runId} />;
}
