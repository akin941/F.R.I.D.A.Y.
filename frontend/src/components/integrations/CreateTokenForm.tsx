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
import { apiPostJson, errorMessage } from "@/lib/api-client";
import { useWorkspace } from "@/contexts/workspace-context";
import { tr } from "@/lib/i18n/tr";
import type { ApiPersonalAccessTokenCreated, PatKind } from "@/types/api";

const KINDS: PatKind[] = ["cli", "openai", "hook"];

// Lifetimes offered at mint (days). The backend default is 90 and its cap
// 365 (PAT_DEFAULT_TTL_DAYS / PAT_MAX_TTL_DAYS).
const EXPIRY_OPTIONS = [
  { days: "30", label: "30 gün" },
  { days: "90", label: "90 gün" },
  { days: "180", label: "180 gün" },
  { days: "365", label: "1 yıl" },
];
const DEFAULT_EXPIRY_DAYS = "90";

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
  const [expiryDays, setExpiryDays] = useState(DEFAULT_EXPIRY_DAYS);
  const [kind, setKind] = useState<PatKind>("cli");
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
        kind,
        expires_in_days: Number(expiryDays),
      });
      onCreated(token);
      setName("");
    } catch (err) {
      setError(errorMessage(err, "Token oluşturulamadı."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Yeni kişisel erişim token&apos;ı</CardTitle>
      </CardHeader>
      <form onSubmit={handleSubmit}>
        <CardContent className="flex flex-col gap-3">
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="token-name">İsim</Label>
            <Input
              id="token-name"
              placeholder="örn. dizüstü cli"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="token-kind">Tür</Label>
            <Select value={kind} onValueChange={(value) => setKind(value as PatKind)}>
              <SelectTrigger id="token-kind" aria-label="Token türü">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KINDS.map((option) => (
                  <SelectItem key={option} value={option}>
                    {tr.token.kind[option]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{tr.token.kindHint[kind]}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="token-workspace">Çalışma alanı</Label>
            <Select value={workspaceId} onValueChange={setWorkspaceId}>
              <SelectTrigger id="token-workspace" aria-label="Token çalışma alanı">
                <SelectValue placeholder="Çalışma alanı seç" />
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
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="token-expiry">Geçerlilik süresi</Label>
            <Select value={expiryDays} onValueChange={setExpiryDays}>
              <SelectTrigger id="token-expiry" aria-label="Token geçerlilik süresi">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EXPIRY_OPTIONS.map((option) => (
                  <SelectItem key={option.days} value={option.days}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
        <CardFooter className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>İptal</Button>
          <Button type="submit" disabled={submitting || !name.trim() || !workspaceId}>
            {submitting ? "Oluşturuluyor…" : "Token oluştur"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
