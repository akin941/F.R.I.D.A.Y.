import { SkillDetailView } from "@/components/skills/SkillDetailView";

export default async function SkillPage({ params }: { params: Promise<{ skillId: string }> }) {
  const { skillId } = await params;
  return <SkillDetailView skillId={skillId} />;
}
