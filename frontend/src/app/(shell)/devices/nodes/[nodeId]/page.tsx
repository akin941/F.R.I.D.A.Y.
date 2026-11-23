import { NodeDetailPage } from "@/components/nodes/NodeDetail";

export default async function DeviceNodePage({
  params,
}: {
  params: Promise<{ nodeId: string }>;
}) {
  const { nodeId } = await params;
  return <NodeDetailPage nodeId={nodeId} />;
}
