"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkspace } from "@/contexts/workspace-context";

export function WorkspaceSwitcher() {
  const { workspaces, activeWorkspace, setActiveWorkspaceId, loading } = useWorkspace();

  if (loading) {
    return <Skeleton className="h-9 w-full" />;
  }

  if (workspaces.length === 0) {
    return <p className="text-sm text-muted-foreground">No workspace available.</p>;
  }

  return (
    <Select value={activeWorkspace?.id} onValueChange={setActiveWorkspaceId}>
      <SelectTrigger className="w-full" aria-label="Active workspace">
        <SelectValue placeholder="Select workspace" />
      </SelectTrigger>
      <SelectContent>
        {workspaces.map((workspace) => (
          <SelectItem key={workspace.id} value={workspace.id}>
            {workspace.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
