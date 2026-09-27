"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { TaskCard } from "@/components/tasks/TaskCard";
import { TaskCreateForm } from "@/components/tasks/TaskCreateForm";
import { TASK_STATUS_LABELS, TASK_STATUSES } from "@/components/tasks/labels";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson } from "@/lib/api-client";
import type { ApiTask, TaskStatus } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { DeepLinkTarget, useDeepLinkTarget } from "@/components/shell/deep-link-target";
import { cn } from "@/lib/utils";

export function TaskView() {
  const { activeWorkspace } = useWorkspace();

  const [tasks, setTasks] = useState<ApiTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<TaskStatus | "all">("all");
  const [creating, setCreating] = useState(false);

  const activeWorkspaceIdRef = useRef<string | null>(null);

  const loadTasks = useCallback(async (workspaceId: string) => {
    setLoading(true);
    setLoadError(null);
    try {
      const list = await apiGetJson<ApiTask[]>(
        `/api/tasks?workspace=${encodeURIComponent(workspaceId)}`,
      );
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setTasks(list);
    } catch {
      if (activeWorkspaceIdRef.current === workspaceId) {
        setLoadError("Görevler yüklenemedi.");
      }
    } finally {
      if (activeWorkspaceIdRef.current === workspaceId) setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount/workspace-change, the standard data-fetching effect pattern */
  useEffect(() => {
    const workspaceId = activeWorkspace?.id ?? null;
    activeWorkspaceIdRef.current = workspaceId;
    setTasks([]);
    // A workspace switch must not leave a stale create-form draft mounted
    // (mirrors MemoryView's guard): TaskCreateForm is also re-keyed by
    // workspace id below, which resets its internal fields on remount, but
    // `creating` itself is closed here too so a draft started in workspace A
    // never lingers visibly into B.
    setCreating(false);
    if (workspaceId) {
      void loadTasks(workspaceId);
    }
  }, [activeWorkspace, loadTasks]);
  /* eslint-enable react-hooks/set-state-in-effect */

  function handleChanged(updated: ApiTask) {
    setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
  }

  function handleDeleted(id: string) {
    setTasks((prev) => prev.filter((t) => t.id !== id));
  }

  function handleCreated(task: ApiTask) {
    // Discard a creation response that resolves after the user has already
    // switched to a different workspace (mirrors MemoryView's guard).
    if (activeWorkspaceIdRef.current !== task.workspace) return;
    setTasks((prev) => [task, ...prev]);
    setCreating(false);
  }

  // `?task=<id>` from a chat "Görev oluşturuldu" link.
  const linkedTaskId = useDeepLinkTarget("task", !loading && tasks.length > 0);
  const visible = tasks.filter((t) => statusFilter === "all" || t.status === statusFilter);

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        Kullanılabilir çalışma alanı yok.
      </div>
    );
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Görevler</h1>
          <p className="text-sm text-muted-foreground">Yapılacaklar listen.</p>
        </div>
        <Button onClick={() => setCreating((v) => !v)}>{creating ? "İptal" : "Yeni görev"}</Button>
      </div>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {creating ? (
        // Keyed by workspace id, same reasoning as MemoryCreateForm above.
        <TaskCreateForm
          key={activeWorkspace.id}
          workspaceId={activeWorkspace.id}
          onCreated={handleCreated}
          onCancel={() => setCreating(false)}
        />
      ) : null}

      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <section className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-medium tracking-tight">Tüm görevler</h2>
            <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as TaskStatus | "all")}>
              <SelectTrigger aria-label="Durum filtresi">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Tüm durumlar</SelectItem>
                {TASK_STATUSES.map((status) => (
                  <SelectItem key={status} value={status}>
                    {TASK_STATUS_LABELS[status]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {visible.length === 0 ? (
            <p className="text-sm text-muted-foreground">Bu filtrelere uyan görev yok.</p>
          ) : (
            <div className="flex flex-col gap-3">
              {visible.map((task) => (
                <DeepLinkTarget key={task.id} kind="task" id={task.id} active={linkedTaskId === task.id}>
                  <TaskCard task={task} onChanged={handleChanged} onDeleted={handleDeleted} />
                </DeepLinkTarget>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
