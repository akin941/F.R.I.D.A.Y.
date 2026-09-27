"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkspace } from "@/contexts/workspace-context";
import { NewFirmDialog } from "@/components/shell/NewFirmDialog";

export function WorkspaceSwitcher() {
  const { user, workspaces, activeWorkspace, setActiveWorkspaceId, loading } = useWorkspace();

  if (loading) {
    return <Skeleton className="h-9 w-full" />;
  }

  // Firm creation is owner-only (Phase 4 branch 2); the backend enforces it.
  const newFirm = user?.is_owner ? <NewFirmDialog /> : null;

  if (workspaces.length === 0) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-sm text-muted-foreground">Kullanılabilir çalışma alanı yok.</p>
        {newFirm}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <Select value={activeWorkspace?.id} onValueChange={setActiveWorkspaceId}>
        <SelectTrigger className="w-full" aria-label="Aktif çalışma alanı">
          <SelectValue placeholder="Çalışma alanı seç" />
        </SelectTrigger>
        <SelectContent>
          {workspaces.map((workspace) => (
            <SelectItem key={workspace.id} value={workspace.id}>
              {workspace.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {newFirm}
    </div>
  );
}
