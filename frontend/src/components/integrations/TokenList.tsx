"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { apiPostJson } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
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
  // Evaluated once per mount (render must stay pure); a token expiring
  // while the page is open shows as expired on the next load.
  const [renderedAt] = useState(() => Date.now());
  const expired = new Date(token.expires_at).getTime() <= renderedAt;

  // Runs only from the confirmation dialog, which reports a failure.
  async function handleRevoke() {
    onRevoked(await apiPostJson<ApiPersonalAccessToken>(`/api/auth/tokens/${token.id}/revoke`));
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{token.name}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
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
            <ConfirmDialog
              trigger={
                <Button variant="outline" size="sm">
                  İptal et
                </Button>
              }
              title={tr.confirm.token.title}
              description={tr.confirm.token.description(token.name)}
              confirmLabel={tr.confirm.token.confirm}
              busyLabel={tr.confirm.token.busy}
              errorFallback={tr.confirm.token.failed}
              onConfirm={handleRevoke}
            />
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
