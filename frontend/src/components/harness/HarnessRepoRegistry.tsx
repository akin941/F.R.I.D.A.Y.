"use client";

import { useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { FolderGit2, Plus } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useHarnessRepos } from "@/components/harness/queries";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiDelete, apiPatchJson, apiPostJson, errorMessage } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import { queryKeys } from "@/lib/query/keys";
import type { ApiHarnessRepo } from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

const copy = tr.harness.repos;

interface RepoFields {
  name: string;
  git_url: string;
  default_branch: string;
  enabled: boolean;
  sensitive: boolean;
  test_command: string;
}

const EMPTY_FIELDS: RepoFields = {
  name: "",
  git_url: "",
  default_branch: "main",
  enabled: false,
  sensitive: false,
  test_command: "",
};

function fieldsOf(repo: ApiHarnessRepo): RepoFields {
  return {
    name: repo.name,
    git_url: repo.git_url,
    default_branch: repo.default_branch,
    enabled: repo.enabled,
    sensitive: repo.sensitive,
    test_command: repo.test_command,
  };
}

/** Route `/harness/repos`: the registry of the active workspace. */
export function HarnessRepoRegistry() {
  const { activeWorkspace } = useWorkspace();
  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {tr.common.noWorkspace}
      </div>
    );
  }
  return <RepoRegistry key={activeWorkspace.id} workspaceId={activeWorkspace.id} />;
}

/**
 * The harness repo registry (Phase 4 branch 5) over the existing
 * list/create/detail/patch/delete API. Curator-only on the backend (a
 * member gets the 403 as an error state). A repo is inert until enabled;
 * deleting a repo with job history is refused by the backend (disable it
 * instead). How the ADR-004 self-modification guard sees each repo is
 * shown read-only -- nothing here can change it.
 */
function RepoRegistry({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const repos = useHarnessRepos(workspaceId);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ApiHarnessRepo | null>(null);
  const reposKey = queryKeys.harnessRepos(workspaceId);

  function upsert(repo: ApiHarnessRepo) {
    queryClient.setQueryData<ApiHarnessRepo[]>(reposKey, (prev = []) =>
      prev.some((r) => r.id === repo.id)
        ? prev.map((r) => (r.id === repo.id ? repo : r))
        : [repo, ...prev],
    );
  }

  async function handleDelete(repo: ApiHarnessRepo) {
    await apiDelete(`/api/harness/repos/${repo.id}`);
    queryClient.setQueryData<ApiHarnessRepo[]>(reposKey, (prev = []) =>
      prev.filter((r) => r.id !== repo.id),
    );
  }

  const unconfigured = (repos.data ?? []).some((r) => r.self_repo_status === "unconfigured");

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <Button variant="outline" size="sm" className="self-start" asChild>
        <Link href="/harness">&larr; {tr.harness.console.backToConsole}</Link>
      </Button>
      <PageHeader
        title={copy.title}
        description={copy.description}
        actions={
          <Button onClick={() => setCreating(true)} disabled={!repos.data}>
            <Plus aria-hidden />
            {copy.create}
          </Button>
        }
      />

      {unconfigured ? (
        <Alert>
          <AlertDescription>{copy.selfRepoUnconfigured}</AlertDescription>
        </Alert>
      ) : null}

      {repos.isError && !repos.data ? (
        <ErrorState
          error={repos.error}
          title={copy.loadFailed}
          onRetry={() => void repos.refetch()}
          retrying={repos.isFetching}
        />
      ) : repos.isPending ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : repos.data.length === 0 ? (
        <EmptyState icon={FolderGit2} title={copy.empty} description={copy.emptyHint} />
      ) : (
        <ul className="flex flex-col gap-3">
          {repos.data.map((repo) => (
            <li key={repo.id}>
              <RepoCard
                repo={repo}
                onEdit={() => setEditing(repo)}
                onDelete={() => handleDelete(repo)}
              />
            </li>
          ))}
        </ul>
      )}

      <RepoFormDialog
        open={creating}
        onOpenChange={setCreating}
        title={copy.createTitle}
        submitLabel={copy.createSubmit}
        initial={EMPTY_FIELDS}
        errorFallback={copy.createFailed}
        onSubmit={async (fields) => {
          upsert(
            await apiPostJson<ApiHarnessRepo>("/api/harness/repos", {
              workspace: workspaceId,
              ...fields,
            }),
          );
        }}
      />
      {editing ? (
        <RepoFormDialog
          key={editing.id}
          open
          onOpenChange={(open) => {
            if (!open) setEditing(null);
          }}
          title={copy.editTitle(editing.name)}
          submitLabel={copy.editSubmit}
          initial={fieldsOf(editing)}
          errorFallback={copy.editFailed}
          onSubmit={async (fields) => {
            upsert(await apiPatchJson<ApiHarnessRepo>(`/api/harness/repos/${editing.id}`, fields));
          }}
        />
      ) : null}
    </div>
  );
}

function RepoCard({
  repo,
  onEdit,
  onDelete,
}: {
  repo: ApiHarnessRepo;
  onEdit: () => void;
  onDelete: () => Promise<void>;
}) {
  return (
    <Card data-testid={`repo-${repo.id}`}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {repo.name}
          <Badge variant={repo.enabled ? "default" : "outline"}>
            {repo.enabled ? copy.enabled : copy.disabled}
          </Badge>
          {repo.sensitive ? <Badge variant="outline">{copy.sensitive}</Badge> : null}
          {repo.self_repo_status === "self" ? (
            <Badge variant="outline" title={copy.selfRepoHint}>
              {copy.selfRepo}
            </Badge>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1 text-sm">
        <code className="font-mono text-xs break-all text-muted-foreground">{repo.git_url}</code>
        <p className="text-muted-foreground">
          {copy.defaultBranch}: <code className="font-mono">{repo.default_branch}</code>
        </p>
        {repo.test_command.trim() ? (
          <div className="flex flex-col gap-0.5">
            <span className="text-muted-foreground">{copy.testCommand}:</span>
            <pre className="max-h-32 overflow-auto rounded-md border bg-muted p-2 text-xs whitespace-pre-wrap break-words">
              {repo.test_command}
            </pre>
          </div>
        ) : (
          <p className="text-muted-foreground">{copy.noTestCommand}</p>
        )}
        {repo.self_repo_status === "self" ? (
          <p className="text-muted-foreground">{copy.selfRepoHint}</p>
        ) : null}
      </CardContent>
      <CardFooter className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onEdit}>
          {copy.edit}
        </Button>
        <ConfirmDialog
          trigger={
            <Button variant="destructive" size="sm">
              Sil
            </Button>
          }
          title={tr.confirm.repo.deleteTitle}
          description={tr.confirm.repo.delete(repo.name)}
          confirmLabel={tr.confirm.repo.deleteConfirm}
          busyLabel={tr.confirm.repo.deleting}
          errorFallback={tr.confirm.repo.deleteFailed}
          onConfirm={onDelete}
        />
      </CardFooter>
    </Card>
  );
}

function RepoFormDialog({
  open,
  onOpenChange,
  title,
  submitLabel,
  initial,
  errorFallback,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  submitLabel: string;
  initial: RepoFields;
  errorFallback: string;
  onSubmit: (fields: RepoFields) => Promise<void>;
}) {
  const [fields, setFields] = useState<RepoFields>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set<K extends keyof RepoFields>(key: K, value: RepoFields[K]) {
    setFields((prev) => ({ ...prev, [key]: value }));
  }

  function close(next: boolean) {
    if (busy) return;
    if (!next) {
      setFields(initial);
      setError(null);
    }
    onOpenChange(next);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        ...fields,
        name: fields.name.trim(),
        git_url: fields.git_url.trim(),
        default_branch: fields.default_branch.trim() || "main",
      });
      setBusy(false);
      setFields(initial);
      onOpenChange(false);
    } catch (err) {
      setError(errorMessage(err, errorFallback));
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{copy.formDescription}</DialogDescription>
          </DialogHeader>
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <div className="flex flex-col gap-2">
            <Label htmlFor="repo-name">{copy.name}</Label>
            <Input
              id="repo-name"
              value={fields.name}
              maxLength={255}
              required
              onChange={(e) => set("name", e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="repo-git-url">{copy.gitUrl}</Label>
            <Input
              id="repo-git-url"
              value={fields.git_url}
              maxLength={2000}
              required
              placeholder="git@github.com:owner/repo.git"
              aria-describedby="repo-git-url-hint"
              onChange={(e) => set("git_url", e.target.value)}
            />
            <p id="repo-git-url-hint" className="text-xs text-muted-foreground">
              {copy.gitUrlHint}
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="repo-default-branch">{copy.defaultBranch}</Label>
            <Input
              id="repo-default-branch"
              value={fields.default_branch}
              maxLength={255}
              onChange={(e) => set("default_branch", e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="repo-test-command">{copy.testCommand}</Label>
            <Textarea
              id="repo-test-command"
              value={fields.test_command}
              rows={3}
              maxLength={10000}
              aria-describedby="repo-test-command-hint"
              className="font-mono text-xs"
              onChange={(e) => set("test_command", e.target.value)}
            />
            <p id="repo-test-command-hint" className="text-xs text-muted-foreground">
              {copy.testCommandHint}
            </p>
          </div>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-primary"
              checked={fields.enabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />
            <span>
              {copy.enabledLabel}
              <span className="block text-xs text-muted-foreground">{copy.enabledHint}</span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-primary"
              checked={fields.sensitive}
              onChange={(e) => set("sensitive", e.target.checked)}
            />
            <span>
              {copy.sensitiveLabel}
              <span className="block text-xs text-muted-foreground">{copy.sensitiveHint}</span>
            </span>
          </label>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => close(false)} disabled={busy}>
              {tr.common.cancel}
            </Button>
            <Button
              type="submit"
              disabled={busy || !fields.name.trim() || !fields.git_url.trim()}
            >
              {busy ? copy.saving : submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
