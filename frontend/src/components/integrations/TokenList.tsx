"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { apiPostJson, ApiError } from "@/lib/api-client";
import { useWorkspace } from "@/contexts/workspace-context";
import type { ApiPersonalAccessToken } from "@/types/api";

function TokenRow({
  token,
  workspaceName,
  onRevoked,
}: {
  token: ApiPersonalAccessToken;
  workspaceName: string;
  onRevoked: (token: ApiPersonalAccessToken) => void;
}) {
  const [revoking, setRevoking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRevoke() {
    setRevoking(true);
    setError(null);
    try {
      const updated = await apiPostJson<ApiPersonalAccessToken>(
        `/api/auth/tokens/${token.id}/revoke`,
      );
      onRevoked(updated);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "That token could not be revoked.")
          : "Something went wrong. Please try again.",
      );
    } finally {
      setRevoking(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{token.name}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{workspaceName}</span>
          <span>&middot;</span>
          <span>Created {new Date(token.created_at).toLocaleString()}</span>
          {token.last_used_at ? (
            <>
              <span>&middot;</span>
              <span>Last used {new Date(token.last_used_at).toLocaleString()}</span>
            </>
          ) : null}
        </div>
        {token.revoked_at ? (
          <p className="text-sm text-muted-foreground">
            Revoked {new Date(token.revoked_at).toLocaleString()}
          </p>
        ) : (
          <div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleRevoke()}
              disabled={revoking}
            >
              {revoking ? "Revoking…" : "Revoke"}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function TokenList({
  tokens,
  onChanged,
}: {
  tokens: ApiPersonalAccessToken[];
  onChanged: (token: ApiPersonalAccessToken) => void;
}) {
  const { workspaces } = useWorkspace();

  function workspaceName(workspaceId: string) {
    return workspaces.find((w) => w.id === workspaceId)?.name ?? "Unknown workspace";
  }

  if (tokens.length === 0) {
    return <p className="text-sm text-muted-foreground">No personal access tokens yet.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {tokens.map((token) => (
        <TokenRow
          key={token.id}
          token={token}
          workspaceName={workspaceName(token.workspace)}
          onRevoked={onChanged}
        />
      ))}
    </div>
  );
}
