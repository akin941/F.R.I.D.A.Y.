import { DevicesPanel } from "@/components/integrations/DevicesPanel";
import { NodesSection } from "@/components/nodes/NodesSection";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

export default function DevicesPage() {
  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Cihazlar</h1>
        <p className="text-sm text-muted-foreground">
          Friday&apos;ye yalnızca eşleştirilmiş cihazlar erişir. İptal edilen bir cihaz bir sonraki
          isteğinde oturumunu kaybeder.
        </p>
      </div>
      <DevicesPanel />
      <NodesSection />
    </div>
  );
}
