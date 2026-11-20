"use client";

import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { apiPostJson, errorMessage } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import type { ApiDeployTarget } from "@/types/api";

const copy = tr.deploys.form;

const TEXT_FIELDS = [
  ["name", copy.name, "text"],
  ["environment", copy.environment, "text"],
  ["ssh_host", copy.sshHost, "text"],
  ["ssh_port", copy.sshPort, "number"],
  ["ssh_user", copy.sshUser, "text"],
  ["remote_app", copy.remoteApp, "text"],
  ["repo_git_url", copy.repo, "text"],
  ["tracked_branch", copy.branch, "text"],
  ["health_url", copy.healthUrl, "url"],
  ["health_timeout_seconds", copy.healthTimeout, "number"],
] as const;

type FieldName = (typeof TEXT_FIELDS)[number][0] | "host_public_key";

const INITIAL: Record<FieldName, string> = {
  name: "",
  environment: "production",
  ssh_host: "",
  ssh_port: "22",
  ssh_user: "deploy",
  remote_app: "",
  repo_git_url: "",
  tracked_branch: "main",
  health_url: "",
  health_timeout_seconds: "120",
  host_public_key: "",
};

/**
 * A new deploy target (owner, web). The backend validates every field and
 * always creates it disabled: the owner runs the first status check, then
 * enables it (docs/runbooks/deploy-target-setup.md). Creating a target
 * contacts nothing.
 */
export function DeployTargetForm({
  workspaceId,
  onCreated,
  onCancel,
}: {
  workspaceId: string;
  onCreated: (target: ApiDeployTarget) => void;
  onCancel: () => void;
}) {
  const [values, setValues] = useState(INITIAL);
  const [requireCi, setRequireCi] = useState(true);
  const [autoRollback, setAutoRollback] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set(name: FieldName, value: string) {
    setValues((prev) => ({ ...prev, [name]: value }));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const target = await apiPostJson<ApiDeployTarget>("/api/deploys/targets", {
        workspace: workspaceId,
        ...values,
        ssh_port: Number(values.ssh_port),
        health_timeout_seconds: Number(values.health_timeout_seconds),
        require_ci_success: requireCi,
        auto_rollback: autoRollback,
      });
      onCreated(target);
    } catch (err) {
      setError(errorMessage(err, copy.failed));
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={(event) => void handleSubmit(event)}
      className="flex flex-col gap-3 rounded-lg border p-4"
      aria-label={copy.title}
    >
      <h2 className="text-base font-medium">{copy.title}</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {TEXT_FIELDS.map(([name, label, type]) => (
          <div key={name} className="flex flex-col gap-1">
            <Label htmlFor={`target-${name}`}>{label}</Label>
            <Input
              id={`target-${name}`}
              type={type}
              value={values[name]}
              required={name !== "health_url"}
              onChange={(event) => set(name, event.target.value)}
            />
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="target-host_public_key">{copy.hostKey}</Label>
        <Textarea
          id="target-host_public_key"
          value={values.host_public_key}
          required
          rows={3}
          className="font-mono text-xs"
          onChange={(event) => set("host_public_key", event.target.value)}
        />
        <p className="text-xs text-muted-foreground">{copy.hostKeyHint}</p>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={requireCi} onChange={(e) => setRequireCi(e.target.checked)} />
        {copy.requireCi}
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={autoRollback}
          onChange={(e) => setAutoRollback(e.target.checked)}
        />
        {copy.autoRollback}
      </label>
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
          {tr.common.cancel}
        </Button>
        <Button type="submit" disabled={busy}>
          {busy ? copy.busy : copy.submit}
        </Button>
      </div>
    </form>
  );
}
