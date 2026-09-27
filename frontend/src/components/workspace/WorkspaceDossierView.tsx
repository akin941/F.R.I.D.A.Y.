"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { useWorkspace } from "@/contexts/workspace-context";
import { apiGetJson, apiPatchJson, ApiError } from "@/lib/api-client";
import type {
  ApiWorkspaceDetail,
  ApiWorkspaceProfile,
  ApiWorkspaceProfileDeployTarget,
  ApiWorkspaceProfileRepo,
  ApiWorkspaceProfileService,
} from "@/types/api";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { cn } from "@/lib/utils";

// Only owner/admin may PATCH the profile (backend workspaces/services.py
// `update_profile`'s `_CURATOR_ROLES`) -- gating the edit affordance here is
// UX only, the backend re-enforces this regardless of what the button shows.
const CURATOR_ROLES = new Set(["owner", "admin"]);

function emptyProfile(): ApiWorkspaceProfile {
  return { repos: [], services: [], deploy_targets: [], notes: "" };
}

function cloneProfile(profile: ApiWorkspaceProfile): ApiWorkspaceProfile {
  return {
    repos: (profile.repos ?? []).map((r) => ({ ...r })),
    services: (profile.services ?? []).map((s) => ({ ...s })),
    deploy_targets: (profile.deploy_targets ?? []).map((d) => ({ ...d })),
    notes: profile.notes ?? "",
  };
}

// Rows with a blank required field are dropped rather than submitted --
// mirrors the backend's own required-field rejection (workspaces/serializers.py)
// but avoids surfacing a validation error for a row the user simply hasn't
// filled in yet (e.g. just clicked "add"). Optional fields left blank are
// omitted entirely rather than sent as "" -- both are accepted by the
// backend, omitting keeps the stored profile tidy.
function cleanProfileForSubmit(profile: ApiWorkspaceProfile): ApiWorkspaceProfile {
  return {
    repos: (profile.repos ?? [])
      .filter((r) => r.name.trim() && r.url.trim())
      .map((r) => ({
        name: r.name.trim(),
        url: r.url.trim(),
        ...(r.default_branch?.trim() ? { default_branch: r.default_branch.trim() } : {}),
      })),
    services: (profile.services ?? [])
      .filter((s) => s.name.trim())
      .map((s) => ({
        name: s.name.trim(),
        ...(s.kind?.trim() ? { kind: s.kind.trim() } : {}),
        ...(s.url?.trim() ? { url: s.url.trim() } : {}),
      })),
    deploy_targets: (profile.deploy_targets ?? [])
      .filter((d) => d.name.trim())
      .map((d) => ({
        name: d.name.trim(),
        ...(d.host?.trim() ? { host: d.host.trim() } : {}),
        ...(d.notes?.trim() ? { notes: d.notes.trim() } : {}),
      })),
    notes: (profile.notes ?? "").trim(),
  };
}

export function WorkspaceDossierView() {
  const { activeWorkspace } = useWorkspace();

  const [detail, setDetail] = useState<ApiWorkspaceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ApiWorkspaceProfile>(emptyProfile());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const activeWorkspaceIdRef = useRef<string | null>(null);

  const load = useCallback(async (workspaceId: string) => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await apiGetJson<ApiWorkspaceDetail>(
        `/api/workspaces/${encodeURIComponent(workspaceId)}`,
      );
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setDetail(data);
    } catch {
      if (activeWorkspaceIdRef.current === workspaceId) {
        setLoadError("Firma profili yüklenemedi.");
      }
    } finally {
      if (activeWorkspaceIdRef.current === workspaceId) setLoading(false);
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount/workspace-change, the standard data-fetching effect pattern */
  useEffect(() => {
    const workspaceId = activeWorkspace?.id ?? null;
    activeWorkspaceIdRef.current = workspaceId;
    setDetail(null);
    // A workspace switch must not leave a stale edit draft open against the
    // previous workspace's profile (mirrors MemoryView's same-shaped guard
    // for its create-form draft). `submitting` must be reset here too
    // (Codex round-1 P2): handleSubmit's own `finally` skips resetting it
    // when the workspace has already changed by the time the PATCH
    // resolves, since it deliberately discards stale responses -- without
    // this, a save started just before switching workspaces would leave
    // Save/Cancel permanently disabled on the newly active workspace.
    setEditing(false);
    setSubmitError(null);
    setSubmitting(false);
    if (workspaceId) {
      void load(workspaceId);
    }
  }, [activeWorkspace, load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  function startEditing() {
    if (!detail) return;
    setDraft(cloneProfile(detail.profile));
    setSubmitError(null);
    setEditing(true);
  }

  function cancelEditing() {
    setEditing(false);
    setSubmitError(null);
  }

  function updateRepo(index: number, patch: Partial<ApiWorkspaceProfileRepo>) {
    setDraft((prev) => ({
      ...prev,
      repos: (prev.repos ?? []).map((r, i) => (i === index ? { ...r, ...patch } : r)),
    }));
  }
  function addRepo() {
    setDraft((prev) => ({ ...prev, repos: [...(prev.repos ?? []), { name: "", url: "" }] }));
  }
  function removeRepo(index: number) {
    setDraft((prev) => ({ ...prev, repos: (prev.repos ?? []).filter((_, i) => i !== index) }));
  }

  function updateService(index: number, patch: Partial<ApiWorkspaceProfileService>) {
    setDraft((prev) => ({
      ...prev,
      services: (prev.services ?? []).map((s, i) => (i === index ? { ...s, ...patch } : s)),
    }));
  }
  function addService() {
    setDraft((prev) => ({ ...prev, services: [...(prev.services ?? []), { name: "" }] }));
  }
  function removeService(index: number) {
    setDraft((prev) => ({
      ...prev,
      services: (prev.services ?? []).filter((_, i) => i !== index),
    }));
  }

  function updateDeployTarget(index: number, patch: Partial<ApiWorkspaceProfileDeployTarget>) {
    setDraft((prev) => ({
      ...prev,
      deploy_targets: (prev.deploy_targets ?? []).map((d, i) =>
        i === index ? { ...d, ...patch } : d,
      ),
    }));
  }
  function addDeployTarget() {
    setDraft((prev) => ({
      ...prev,
      deploy_targets: [...(prev.deploy_targets ?? []), { name: "" }],
    }));
  }
  function removeDeployTarget(index: number) {
    setDraft((prev) => ({
      ...prev,
      deploy_targets: (prev.deploy_targets ?? []).filter((_, i) => i !== index),
    }));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!detail) return;
    const workspaceId = detail.id;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const updated = await apiPatchJson<ApiWorkspaceDetail>(
        `/api/workspaces/${encodeURIComponent(workspaceId)}`,
        { profile: cleanProfileForSubmit(draft) },
      );
      // Discard a response that resolves after the user has already
      // switched to a different workspace -- otherwise workspace A's saved
      // dossier could overwrite workspace B's displayed state (mirrors
      // MemoryView's handleCreated/handleVaultExportRequest workspace
      // guards).
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setDetail(updated);
      setEditing(false);
    } catch (err) {
      if (activeWorkspaceIdRef.current !== workspaceId) return;
      setSubmitError(
        err instanceof ApiError
          ? (err.body.detail ?? "Profil kaydedilemedi.")
          : "Profil kaydedilemedi.",
      );
    } finally {
      if (activeWorkspaceIdRef.current === workspaceId) setSubmitting(false);
    }
  }

  if (!activeWorkspace) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        Kullanılabilir çalışma alanı yok.
      </div>
    );
  }

  const canEdit = CURATOR_ROLES.has(detail?.role ?? "");

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-6 p-6")}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Firma dosyası</h1>
          <p className="text-sm text-muted-foreground">
            Repolar, servisler, deploy hedefleri ve notlar — Jarvis&apos;in sohbet
            bağlamına dahil ettiği çalışma alanı özeti.
          </p>
        </div>
        {!editing && canEdit ? <Button onClick={startEditing}>Düzenle</Button> : null}
      </div>

      {loadError ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : null}

      {!loading && detail && !editing ? (
        <DossierReadView profile={detail.profile} />
      ) : null}

      {!loading && detail && editing ? (
        <Card>
          <form onSubmit={handleSubmit}>
            <CardHeader>
              <CardTitle>Firma dosyasını düzenle</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-6">
              {submitError ? (
                <Alert variant="destructive" role="alert">
                  <AlertDescription>{submitError}</AlertDescription>
                </Alert>
              ) : null}

              <section className="flex flex-col gap-3">
                <h2 className="text-sm font-medium">Repolar</h2>
                {(draft.repos ?? []).map((repo, index) => (
                  <div key={index} className="flex flex-wrap items-end gap-2">
                    <div className="flex flex-1 flex-col gap-1">
                      <Label htmlFor={`repo-name-${index}`}>Ad</Label>
                      <Input
                        id={`repo-name-${index}`}
                        value={repo.name}
                        onChange={(e) => updateRepo(index, { name: e.target.value })}
                      />
                    </div>
                    <div className="flex flex-1 flex-col gap-1">
                      <Label htmlFor={`repo-url-${index}`}>URL</Label>
                      <Input
                        id={`repo-url-${index}`}
                        value={repo.url}
                        onChange={(e) => updateRepo(index, { url: e.target.value })}
                      />
                    </div>
                    <div className="flex flex-1 flex-col gap-1">
                      <Label htmlFor={`repo-branch-${index}`}>Varsayılan dal</Label>
                      <Input
                        id={`repo-branch-${index}`}
                        value={repo.default_branch ?? ""}
                        onChange={(e) => updateRepo(index, { default_branch: e.target.value })}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label="Repoyu kaldır"
                      onClick={() => removeRepo(index)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
                <Button type="button" variant="outline" onClick={addRepo} className="w-fit">
                  Repo ekle
                </Button>
              </section>

              <Separator />

              <section className="flex flex-col gap-3">
                <h2 className="text-sm font-medium">Servisler</h2>
                {(draft.services ?? []).map((service, index) => (
                  <div key={index} className="flex flex-wrap items-end gap-2">
                    <div className="flex flex-1 flex-col gap-1">
                      <Label htmlFor={`service-name-${index}`}>Ad</Label>
                      <Input
                        id={`service-name-${index}`}
                        value={service.name}
                        onChange={(e) => updateService(index, { name: e.target.value })}
                      />
                    </div>
                    <div className="flex flex-1 flex-col gap-1">
                      <Label htmlFor={`service-kind-${index}`}>Tür</Label>
                      <Input
                        id={`service-kind-${index}`}
                        value={service.kind ?? ""}
                        onChange={(e) => updateService(index, { kind: e.target.value })}
                      />
                    </div>
                    <div className="flex flex-1 flex-col gap-1">
                      <Label htmlFor={`service-url-${index}`}>URL</Label>
                      <Input
                        id={`service-url-${index}`}
                        value={service.url ?? ""}
                        onChange={(e) => updateService(index, { url: e.target.value })}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label="Servisi kaldır"
                      onClick={() => removeService(index)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
                <Button type="button" variant="outline" onClick={addService} className="w-fit">
                  Servis ekle
                </Button>
              </section>

              <Separator />

              <section className="flex flex-col gap-3">
                <h2 className="text-sm font-medium">Deploy hedefleri</h2>
                <p className="text-xs text-muted-foreground">
                  Sadece meta veri — dağıtım kimlik bilgileri/sırları asla buraya girilmemeli.
                </p>
                {(draft.deploy_targets ?? []).map((target, index) => (
                  <div key={index} className="flex flex-wrap items-end gap-2">
                    <div className="flex flex-1 flex-col gap-1">
                      <Label htmlFor={`deploy-name-${index}`}>Ad</Label>
                      <Input
                        id={`deploy-name-${index}`}
                        value={target.name}
                        onChange={(e) => updateDeployTarget(index, { name: e.target.value })}
                      />
                    </div>
                    <div className="flex flex-1 flex-col gap-1">
                      <Label htmlFor={`deploy-host-${index}`}>Host</Label>
                      <Input
                        id={`deploy-host-${index}`}
                        value={target.host ?? ""}
                        onChange={(e) => updateDeployTarget(index, { host: e.target.value })}
                      />
                    </div>
                    <div className="flex flex-1 flex-col gap-1">
                      <Label htmlFor={`deploy-notes-${index}`}>Not</Label>
                      <Input
                        id={`deploy-notes-${index}`}
                        value={target.notes ?? ""}
                        onChange={(e) => updateDeployTarget(index, { notes: e.target.value })}
                      />
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label="Deploy hedefini kaldır"
                      onClick={() => removeDeployTarget(index)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
                <Button type="button" variant="outline" onClick={addDeployTarget} className="w-fit">
                  Deploy hedefi ekle
                </Button>
              </section>

              <Separator />

              <section className="flex flex-col gap-2">
                <Label htmlFor="profile-notes">Notlar</Label>
                <Textarea
                  id="profile-notes"
                  rows={4}
                  value={draft.notes ?? ""}
                  onChange={(e) => setDraft((prev) => ({ ...prev, notes: e.target.value }))}
                />
              </section>
            </CardContent>
            <CardFooter className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={cancelEditing} disabled={submitting}>
                İptal
              </Button>
              <Button type="submit" disabled={submitting}>
                {submitting ? "Kaydediliyor…" : "Kaydet"}
              </Button>
            </CardFooter>
          </form>
        </Card>
      ) : null}
    </div>
  );
}

function DossierReadView({ profile }: { profile: ApiWorkspaceProfile }) {
  const repos = profile.repos ?? [];
  const services = profile.services ?? [];
  const deployTargets = profile.deploy_targets ?? [];
  const notes = profile.notes ?? "";
  const isEmpty =
    repos.length === 0 && services.length === 0 && deployTargets.length === 0 && !notes;

  if (isEmpty) {
    return (
      <p className="text-sm text-muted-foreground">
        Bu çalışma alanı için henüz bir firma dosyası kaydedilmemiş.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-medium text-muted-foreground">Repolar</h2>
        {repos.length === 0 ? (
          <p className="text-sm text-muted-foreground">Kayıtlı repo yok.</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm">
            {repos.map((repo, index) => (
              <li key={index}>
                <span className="font-medium">{repo.name}</span> — {repo.url}
                {repo.default_branch ? ` (${repo.default_branch})` : ""}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-medium text-muted-foreground">Servisler</h2>
        {services.length === 0 ? (
          <p className="text-sm text-muted-foreground">Kayıtlı servis yok.</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm">
            {services.map((service, index) => (
              <li key={index}>
                <span className="font-medium">{service.name}</span>
                {service.kind ? ` (${service.kind})` : ""}
                {service.url ? ` — ${service.url}` : ""}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-medium text-muted-foreground">Deploy hedefleri</h2>
        {deployTargets.length === 0 ? (
          <p className="text-sm text-muted-foreground">Kayıtlı deploy hedefi yok.</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm">
            {deployTargets.map((target, index) => (
              <li key={index}>
                <span className="font-medium">{target.name}</span>
                {target.host ? ` — ${target.host}` : ""}
                {target.notes ? ` (${target.notes})` : ""}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-medium text-muted-foreground">Notlar</h2>
        {notes ? (
          <p className="whitespace-pre-wrap text-sm">{notes}</p>
        ) : (
          <p className="text-sm text-muted-foreground">Not yok.</p>
        )}
      </section>
    </div>
  );
}
