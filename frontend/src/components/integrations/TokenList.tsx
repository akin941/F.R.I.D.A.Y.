"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { apiPostJson, errorMessage } from "@/lib/api-client";
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
  // Evaluated once per mount (render must stay pure); a token expiring
  // while the page is open shows as expired on the next load.
  const [renderedAt] = useState(() => Date.now());
  const expired = new Date(token.expires_at).getTime() <= renderedAt;

  async function handleRevoke() {
    setRevoking(true);
    setError(null);
    try {
      const updated = await apiPostJson<ApiPersonalAccessToken>(
        `/api/auth/tokens/${token.id}/revoke`,
      );
      onRevoked(updated);
    } catch (err) {
      setError(errorMessage(err, "Token iptal edilemedi."));
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
          <span>Oluşturuldu {new Date(token.created_at).toLocaleString()}</span>
          {token.last_used_at ? (
            <>
              <span>&middot;</span>
              <span>Son kullanım {new Date(token.last_used_at).toLocaleString()}</span>
            </>
          ) : null}
          {!token.revoked_at && !expired ? (
            <>
              <span>&middot;</span>
              <span>Son geçerlilik {new Date(token.expires_at).toLocaleString()}</span>
            </>
          ) : null}
        </div>
        {token.revoked_at ? (
          <p className="text-sm text-muted-foreground">
            İptal edildi {new Date(token.revoked_at).toLocaleString()}
          </p>
        ) : expired ? (
          <p className="text-sm text-muted-foreground">
            Süresi doldu {new Date(token.expires_at).toLocaleString()}
          </p>
        ) : (
          <div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleRevoke()}
              disabled={revoking}
            >
              {revoking ? "İptal ediliyor…" : "İptal et"}
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
    return workspaces.find((w) => w.id === workspaceId)?.name ?? "Bilinmeyen çalışma alanı";
  }

  if (tokens.length === 0) {
    return <p className="text-sm text-muted-foreground">Henüz kişisel erişim token&apos;ı yok.</p>;
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
