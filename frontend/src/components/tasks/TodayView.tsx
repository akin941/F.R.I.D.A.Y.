"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { TASK_PRIORITY_LABELS } from "@/components/tasks/labels";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson, apiPostJson, errorMessage } from "@/lib/api-client";
import type { ApiTask } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

/**
 * The *client's* local calendar date as YYYY-MM-DD -- deliberately not
 * `Date.toISOString().slice(0, 10)`, which reports the UTC calendar day and
 * would show the wrong task set to a user whose local day has already
 * rolled over (or not yet) relative to UTC (brief §2 round-1 finding: the
 * server must never compute "today" itself).
 */
export function localDateString(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function TodayTaskItem({
  task,
  onCompleted,
}: {
  task: ApiTask;
  onCompleted: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleComplete() {
    setBusy(true);
    setError(null);
    try {
      await apiPostJson<ApiTask>(`/api/tasks/${task.id}/complete`);
      onCompleted(task.id);
    } catch (err) {
      setError(errorMessage(err, "Görev tamamlanamadı."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{task.title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{TASK_PRIORITY_LABELS[task.priority]}</span>
          {task.due_date ? (
            <>
              <span>&middot;</span>
              <span>Son tarih {task.due_date}</span>
            </>
          ) : null}
        </div>
      </CardContent>
      <CardFooter className="flex justify-end">
        <Button size="sm" onClick={() => void handleComplete()} disabled={busy}>Tamamla</Button>
      </CardFooter>
    </Card>
  );
}

export function TodayView() {
  const { activeWorkspace } = useWorkspace();

  const [tasks, setTasks] = useState<ApiTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const activeWorkspaceIdRef = useRef<string | null>(null);

  const loadTasks = useCallback(async (workspaceId: string) => {
    setLoading(true);
    setLoadError(null);
    try {
      const today = localDateString(new Date());
      const list = await apiGetJson<ApiTask[]>(
        `/api/tasks?workspace=${encodeURIComponent(workspaceId)}&status=open&due_before=${today}`,
      );
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setTasks(list);
    } catch {
      if (activeWorkspaceIdRef.current === workspaceId) {
        setLoadError("Bugünün görevleri yüklenemedi.");
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
    if (workspaceId) {
      void loadTasks(workspaceId);
    }
  }, [activeWorkspace, loadTasks]);
  /* eslint-enable react-hooks/set-state-in-effect */

  function handleCompleted(id: string) {
    // A completed task is no longer `open`, so it drops off this filtered
    // view -- no need to re-fetch.
    setTasks((prev) => prev.filter((t) => t.id !== id));
  }

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        Kullanılabilir çalışma alanı yok.
      </div>
    );
  }

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-6")}>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Bugün</h1>
        <p className="text-sm text-muted-foreground">Bugün vadesi gelen veya geçen görevler.</p>
      </div>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Card key={i}>
              <CardHeader>
                <Skeleton className="h-4 w-1/2" />
              </CardHeader>
              <CardContent className="flex flex-col gap-2">
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-5/6" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : tasks.length === 0 ? (
        <p className="text-sm text-muted-foreground">Bugün vadesi gelen bir şey yok. Keyfini çıkar.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {tasks.map((task) => (
            <TodayTaskItem key={task.id} task={task} onCompleted={handleCompleted} />
          ))}
        </div>
      )}
    </div>
  );
}
