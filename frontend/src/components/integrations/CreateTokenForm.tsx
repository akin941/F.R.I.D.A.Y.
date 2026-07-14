"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { apiPostJson, ApiError } from "@/lib/api-client";
import { useWorkspace } from "@/contexts/workspace-context";
import type { ApiPersonalAccessTokenCreated } from "@/types/api";

/**
 * A token is bound to one workspace for its lifetime (brief §2/§3), which
 * may not be whichever workspace the sidebar switcher currently has active
 * -- this form carries its own workspace picker rather than reusing
 * `activeWorkspace` implicitly.
 */
export function CreateTokenForm({
  onCreated,
  onCancel,
}: {
  onCreated: (token: ApiPersonalAccessTokenCreated) => void;
  onCancel: () => void;
}) {
  const { workspaces, activeWorkspace } = useWorkspace();

  const [name, setName] = useState("");
  const [workspaceId, setWorkspaceId] = useState(activeWorkspace?.id ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const token = await apiPostJson<ApiPersonalAccessTokenCreated>("/api/auth/tokens", {
        workspace: workspaceId,
        name,
      });
      onCreated(token);
      setName("");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "That token could not be created.")
          : "Something went wrong. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>New personal access token</CardTitle>
      </CardHeader>
      <form onSubmit={handleSubmit}>
        <CardContent className="flex flex-col gap-3">
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="token-name">Name</Label>
            <Input
              id="token-name"
              placeholder="e.g. laptop cli"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="token-workspace">Workspace</Label>
            <Select value={workspaceId} onValueChange={setWorkspaceId}>
              <SelectTrigger id="token-workspace" aria-label="Token workspace">
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
          </div>
        </CardContent>
        <CardFooter className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" disabled={submitting || !name.trim() || !workspaceId}>
            {submitting ? "Creating…" : "Create token"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
