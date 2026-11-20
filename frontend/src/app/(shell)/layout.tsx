import { Suspense } from "react";
import { WorkspaceProvider } from "@/contexts/workspace-context";
import { Sidebar } from "@/components/shell/Sidebar";
import { SecurityNoticeBanner } from "@/components/shell/SecurityNoticeBanner";
import { WorkspaceDeepLink } from "@/components/shell/WorkspaceDeepLink";
import { VoiceProvider } from "@/components/voice/VoiceProvider";

export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <WorkspaceProvider>
      {/* Wake mode lives here (Phase 6 D6): navigating keeps it armed. */}
      <VoiceProvider>
        {/* useSearchParams needs a Suspense boundary to keep routes static. */}
        <Suspense>
          <WorkspaceDeepLink />
        </Suspense>
        {/* Fixed viewport height (not min-h-screen) with a min-h-0 flex chain:
            every view's own `overflow-y-auto` region is then bounded by the
            viewport, so the sidebar and chat composer stay put while only the
            view's content scrolls. */}
        <div className="flex h-dvh min-h-0 flex-col overflow-hidden md:flex-row">
          <Sidebar />
          <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            <SecurityNoticeBanner />
            {children}
          </main>
        </div>
      </VoiceProvider>
    </WorkspaceProvider>
  );
}
