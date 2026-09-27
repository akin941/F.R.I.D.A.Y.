"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { apiGetJson } from "@/lib/api-client";
import type { ApiHarnessRepo } from "@/types/api";

/**
 * Picker over the workspace's registered, enabled harness repos. Repo
 * registration itself stays an owner-side action outside this UI (no
 * registry editing here) -- the empty state says so rather than offering a
 * raw id field.
 *
 * `requireTestCommand`: implementation jobs fail closed without a
 * configured `test_command` (backend rejects the create with a 400), so
 * those repos are listed but not selectable in that mode.
 */
export function HarnessRepoPicker({
  id,
  workspaceId,
  value,
  onChange,
  requireTestCommand = false,
  disabled = false,
}: {
  id: string;
  workspaceId: string;
  value: string;
  onChange: (repoId: string) => void;
  requireTestCommand?: boolean;
  disabled?: boolean;
}) {
  const [repos, setRepos] = useState<ApiHarnessRepo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [droppedPrefill, setDroppedPrefill] = useState(false);
  const activeWorkspaceIdRef = useRef<string | null>(null);
  // Read (not subscribed to) once the list arrives: a prefilled value
  // (e.g. from a discovery job) that isn't selectable is cleared then.
  const valueRef = useRef(value);
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    valueRef.current = value;
    onChangeRef.current = onChange;
  });

  useEffect(() => {
    activeWorkspaceIdRef.current = workspaceId;
    let cancelled = false;
    apiGetJson<ApiHarnessRepo[]>(`/api/harness/repos?workspace=${encodeURIComponent(workspaceId)}`)
      .then((list) => {
        if (cancelled || activeWorkspaceIdRef.current !== workspaceId) return;
        setRepos(list);
        setLoadError(null);
        const current = valueRef.current;
        if (current && !isSelectable(list, current, requireTestCommand)) {
          setDroppedPrefill(true);
          onChangeRef.current("");
        }
      })
      .catch(() => {
        if (cancelled || activeWorkspaceIdRef.current !== workspaceId) return;
        setLoadError("Depolar yüklenemedi.");
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, requireTestCommand]);

  const enabledRepos = (repos ?? []).filter((repo) => repo.enabled);

  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>Depo</Label>
      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : repos == null ? (
        <Skeleton className="h-8 w-64" />
      ) : enabledRepos.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Bu çalışma alanında kullanılabilir kayıtlı depo yok. Depolar çalışma alanı sahibi
          tarafından kaydedilir; bu ekrandan eklenemez.
        </p>
      ) : (
        <>
          <Select
            value={value}
            onValueChange={(next) => {
              setDroppedPrefill(false);
              onChange(next);
            }}
            disabled={disabled}
          >
            <SelectTrigger id={id} className="w-full max-w-md">
              <SelectValue placeholder="Bir depo seç" />
            </SelectTrigger>
            <SelectContent>
              {enabledRepos.map((repo) => {
                const missingTests = requireTestCommand && !repo.test_command.trim();
                return (
                  <SelectItem key={repo.id} value={repo.id} disabled={missingTests}>
                    {repo.name}
                    {missingTests ? " (test komutu tanımlı değil)" : ""}
                  </SelectItem>
                );
              })}
            </SelectContent>
          </Select>
          {droppedPrefill ? (
            <p role="alert" className="text-sm text-destructive">
              Önceden seçilen depo artık kullanılamıyor (devre dışı, kaldırılmış ya da test
              komutu tanımlı değil). Başka bir depo seç.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}

function isSelectable(repos: ApiHarnessRepo[], value: string, requireTestCommand: boolean) {
  const repo = repos.find((r) => r.id === value);
  return Boolean(repo && repo.enabled && (!requireTestCommand || repo.test_command.trim()));
}
