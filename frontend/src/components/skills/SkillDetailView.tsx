"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Textarea } from "@/components/ui/textarea";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { errorMessage } from "@/lib/api-client";
import {
  DECISION_LABELS,
  PROPOSER_LABELS,
  STATUS_LABELS,
  approveVersion,
  archiveSkill,
  editAndApprove,
  fetchSkill,
  proposeVersion,
  rejectVersion,
  restoreSkill,
  type SkillDetail,
  type SkillVersion,
} from "@/lib/api/skills";
import { diffLines } from "@/lib/skills/diff";
import { cn } from "@/lib/utils";

/**
 * `/skills/<id>` (Phase 12 D14/D20): one skill, the version in use and
 * every proposal with its diff against the version in use. Decisions are
 * one item at a time and carry the hash shown here: if the version changed
 * since the page loaded, the server refuses ("version_changed"). There is
 * no delete; an archived skill can be restored.
 */
export function SkillDetailView({ skillId }: { skillId: string }) {
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setSkill(await fetchSkill(skillId));
    } catch (err) {
      setError(errorMessage(err, "Beceri yüklenemedi."));
    }
  }, [skillId]);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-mount */
  useEffect(() => {
    void load();
  }, [load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function run(action: () => Promise<unknown>, success: string, failure: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      setNotice(success);
      await load();
    } catch (err) {
      setError(errorMessage(err, failure));
    } finally {
      setBusy(false);
    }
  }

  if (skill === null) {
    return (
      <div className="p-6 text-sm text-muted-foreground">
        {error ? <span role="alert">{error}</span> : "Yükleniyor…"}
      </div>
    );
  }

  const currentId = skill.current_version?.id ?? null;
  const current = skill.versions.find((v) => v.id === currentId) ?? null;
  const pending = skill.versions.filter((v) => v.decision === "pending");
  const history = skill.versions.filter((v) => v.decision !== "pending");

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-6")} data-testid="skill-detail">
      <Link href="/skills" className="text-sm text-muted-foreground underline underline-offset-2">
        Beceriler
      </Link>
      <PageHeader
        title={skill.name}
        description={`${STATUS_LABELS[skill.status]} · ${skill.use_count} kullanım`}
      />
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {notice ? (
        <Alert>
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex gap-2">
        {skill.status === "active" || skill.status === "stale" ? (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => archiveSkill(skill.id), "Arşivlendi.", "Arşivlenemedi.")}
          >
            Arşivle
          </Button>
        ) : null}
        {skill.status === "archived" ? (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void run(() => restoreSkill(skill.id), "Geri alındı.", "Geri alınamadı.")}
          >
            Geri al
          </Button>
        ) : null}
      </div>

      {pending.map((version) => (
        <PendingVersion
          key={version.id}
          version={version}
          current={current}
          busy={busy}
          onApprove={() =>
            run(() => approveVersion(version.id, version.sha256), "Onaylandı.", "Onaylanamadı.")
          }
          onEditApprove={(text) =>
            run(
              () => editAndApprove(version.id, version.sha256, text),
              "Düzenlendi ve onaylandı.",
              "Onaylanamadı.",
            )
          }
          onReject={() => run(() => rejectVersion(version.id, version.sha256), "Reddedildi.", "Reddedilemedi.")}
        />
      ))}

      {current ? (
        <Card data-testid="skill-current">
          <CardHeader>
            <CardTitle className="text-base">Kullanımdaki sürüm: {current.version}</CardTitle>
            <p className="font-mono text-xs text-muted-foreground">sha256 {current.sha256.slice(0, 16)}…</p>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <pre className="whitespace-pre-wrap rounded-md bg-muted p-3 text-xs">{current.text}</pre>
            {skill.status !== "archived" ? (
              <NewVersionForm
                initial={current.text}
                busy={busy}
                onSubmit={(text) =>
                  run(() => proposeVersion(skill.id, text), "Yeni sürüm önerildi.", "Kaydedilemedi.")
                }
              />
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {history.length > 0 ? (
        <section aria-label="Sürüm geçmişi" className="flex flex-col gap-1 text-sm">
          <h2 className="font-medium text-muted-foreground">Sürüm geçmişi</h2>
          <ul>
            {history.map((version) => (
              <li key={version.id}>
                Sürüm {version.version}: {DECISION_LABELS[version.decision]} ·{" "}
                {PROPOSER_LABELS[version.proposed_by]}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function PendingVersion({
  version,
  current,
  busy,
  onApprove,
  onEditApprove,
  onReject,
}: {
  version: SkillVersion;
  current: SkillVersion | null;
  busy: boolean;
  onApprove: () => void;
  onEditApprove: (text: string) => void;
  onReject: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(version.text);
  const lines = diffLines(current?.text ?? "", version.text);
  const references = Object.keys(version.references ?? {});
  return (
    <Card data-testid={`pending-version-${version.version}`}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          Sürüm {version.version} <Badge variant="secondary">{DECISION_LABELS[version.decision]}</Badge>
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          {PROPOSER_LABELS[version.proposed_by]} · sha256{" "}
          <span className="font-mono">{version.sha256.slice(0, 16)}…</span>
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs" aria-label="Fark" data-testid="skill-diff">
          {lines.map((line, index) => (
            <div
              key={index}
              className={cn(
                line.op === "add" && "text-green-700 dark:text-green-400",
                line.op === "remove" && "text-red-700 line-through dark:text-red-400",
              )}
            >
              {line.op === "add" ? "+ " : line.op === "remove" ? "- " : "  "}
              {line.text}
            </div>
          ))}
        </pre>
        {references.length > 0 ? (
          <p className="text-xs text-muted-foreground">Ekler: {references.join(", ")}</p>
        ) : null}
        {editing ? (
          <>
            <label htmlFor={`edit-${version.id}`} className="sr-only">
              Düzenlenmiş SKILL.md
            </label>
            <Textarea
              id={`edit-${version.id}`}
              value={text}
              rows={10}
              className="font-mono text-xs"
              onChange={(event) => setText(event.target.value)}
            />
          </>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {editing ? (
            <Button type="button" disabled={busy || !text.trim()} onClick={() => onEditApprove(text)}>
              Düzenlemeyi onayla
            </Button>
          ) : (
            <Button type="button" disabled={busy} onClick={onApprove}>
              Bu sürümü onayla
            </Button>
          )}
          <Button type="button" variant="outline" disabled={busy} onClick={() => setEditing((on) => !on)}>
            {editing ? "Düzenlemeyi bırak" : "Düzenle"}
          </Button>
          <Button type="button" variant="ghost" disabled={busy} onClick={onReject}>
            Reddet
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function NewVersionForm({
  initial,
  busy,
  onSubmit,
}: {
  initial: string;
  busy: boolean;
  onSubmit: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(initial);
  if (!open) {
    return (
      <div>
        <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>
          Yeni sürüm yaz
        </Button>
      </div>
    );
  }
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(text);
      }}
    >
      <label htmlFor="new-version" className="sr-only">
        Yeni sürüm
      </label>
      <Textarea
        id="new-version"
        value={text}
        rows={10}
        className="font-mono text-xs"
        onChange={(event) => setText(event.target.value)}
      />
      <div>
        <Button type="submit" disabled={busy || !text.trim()}>
          Öner
        </Button>
      </div>
    </form>
  );
}
