import { TodayDigestSection } from "@/components/proactive/TodayDigestSection";
import { TodayView } from "@/components/tasks/TodayView";

export default function TodayPage() {
  return (
    <TodayView>
      <TodayDigestSection />
    </TodayView>
  );
}
