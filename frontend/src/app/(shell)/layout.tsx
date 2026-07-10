import { WorkspaceProvider } from "@/contexts/workspace-context";
import { Sidebar } from "@/components/shell/Sidebar";

export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <WorkspaceProvider>
      <div className="flex min-h-screen flex-1 flex-col md:flex-row">
        <Sidebar />
        <main className="flex flex-1 flex-col overflow-hidden">{children}</main>
      </div>
    </WorkspaceProvider>
  );
}
