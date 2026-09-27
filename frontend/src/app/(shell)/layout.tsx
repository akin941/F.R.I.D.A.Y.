import { WorkspaceProvider } from "@/contexts/workspace-context";
import { Sidebar } from "@/components/shell/Sidebar";

export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <WorkspaceProvider>
      {/* Fixed viewport height (not min-h-screen) with a min-h-0 flex chain:
          every view's own `overflow-y-auto` region is then bounded by the
          viewport, so the sidebar and chat composer stay put while only the
          view's content scrolls. */}
      <div className="flex h-dvh min-h-0 flex-col overflow-hidden md:flex-row">
        <Sidebar />
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">{children}</main>
      </div>
    </WorkspaceProvider>
  );
}
