"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Textarea } from "@/components/ui/textarea";
import { VIEW_SCROLL_ROOT } from "@/components/shell/view-scroll";
import { CuratorPanel } from "@/components/skills/CuratorPanel";
import { useWorkspace } from "@/contexts/workspace-context";
import { errorMessage } from "@/lib/api-client";
import {
  STATUS_LABELS,
  createSkillFromText,
  fetchSkills,
  uploadSkill,
  type SkillList,
  type SkillStatus,
  type SkillSummary,
} from "@/lib/api/skills";
import { cn } from "@/lib/utils";

const STATUS_ORDER: SkillStatus[] = ["proposed", "active", "stale", "rejected", "archived"];

export const NEW_SKILL_TEMPLATE = `---
name: yeni-beceri
description: "Bu beceri ne zaman kullanılır? Bir iki cümle."
---
Friday bu tür bir istekte ne yapmalı? Adım adım yaz.
`;

/**
 * `/skills` (Phase 12 D13/D14/D20): the workspace's SKILL.md instruction
 * skills by status, an upload (a SKILL.md or a .zip with references/*.md),
 * and an editor for a typed SKILL.md. Anything new stays "Önerildi" until
 * the owner approves that exact version on its page. A skill is text: it
 * grants no tool, no permission and no execution.
 */
export function SkillsView() {
  const { activeWorkspace } = useWorkspace();
  const workspaceId = activeWorkspace?.id ?? null;
  const [data, setData] = useState<SkillList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState(NEW_SKILL_TEMPLATE);
  const [file, setFile] = useState<File | null>(null);
  const current = useRef<string | null>(null);

  const load = useCallback(async (id: string) => {
    try {
      const list = await fetchSkills(id);
      if (current.current === id) setData(list);
    } catch (err) {
      if (current.current === id) setError(errorMessage(err, "Beceriler yüklenemedi."));
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect -- fetch-on-workspace-change */
  useEffect(() => {
    current.current = workspaceId;
    setData(null);
    setError(null);
    if (workspaceId) void load(workspaceId);
  }, [workspaceId, load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function run(action: () => Promise<unknown>, success: string, failure: string) {
    if (!workspaceId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      setNotice(success);
      await load(workspaceId);
    } catch (err) {
      setError(errorMessage(err, failure));
    } finally {
      setBusy(false);
    }
  }

  if (!workspaceId) {
    return <div className="p-6 text-sm text-muted-foreground">Kullanılabilir çalışma alanı yok.</div>;
  }

  const groups = STATUS_ORDER.map((status) => ({
    status,
    skills: (data?.skills ?? []).filter((skill) => skill.status === status),
  })).filter((group) => group.skills.length > 0);

  return (
    <div className={cn(VIEW_SCROLL_ROOT, "gap-4 p-6")} data-testid="skills-view">
      <PageHeader
        title="Beceriler"
        description={
          "SKILL.md talimatları: Friday'e belirli bir iş türünde nasıl davranacağını anlatır. " +
          "Her sürüm, sen onaylayana kadar kullanılmaz. Beceri yalnızca metindir; araç, izin ya da " +
          "çalıştırma yetkisi vermez."
        }
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
      {data === null && !error ? <p className="text-sm text-muted-foreground">Yükleniyor…</p> : null}
      {data !== null && data.skills.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="skills-empty">
          Henüz beceri yok. Aşağıdan bir SKILL.md yükle ya da yaz.
        </p>
      ) : null}
      <CuratorPanel workspaceId={workspaceId} onChanged={() => void load(workspaceId)} />
      {groups.map((group) => (
        <section key={group.status} aria-label={STATUS_LABELS[group.status]} className="flex flex-col gap-2">
          <h2 className="text-sm font-medium text-muted-foreground">{STATUS_LABELS[group.status]}</h2>
          <ul className="flex flex-col gap-2">
            {group.skills.map((skill) => (
              <SkillRow key={skill.id} skill={skill} />
            ))}
          </ul>
        </section>
      ))}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Yükle</CardTitle>
          <p className="text-sm text-muted-foreground">
            Bir SKILL.md ya da SKILL.md ve en fazla 10 references/*.md içeren bir .zip. Betik, ikili dosya
            ya da başka bir dosya varsa paketin tamamı reddedilir.
          </p>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (file) {
                void run(
                  () => uploadSkill(workspaceId, file),
                  "Yüklendi. Onaylayana kadar kullanılmayacak.",
                  "Yükleme reddedildi.",
                );
              }
            }}
          >
            <label htmlFor="skill-upload" className="sr-only">
              SKILL.md ya da .zip
            </label>
            <input
              id="skill-upload"
              type="file"
              accept=".md,.zip,text/markdown,application/zip"
              disabled={busy}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="text-sm"
            />
            <Button type="submit" disabled={busy || !file}>
              Yükle
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Yaz</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void run(
                () => createSkillFromText(workspaceId, draft),
                "Önerildi. Onaylayana kadar kullanılmayacak.",
                "Beceri kaydedilemedi.",
              );
            }}
          >
            <label htmlFor="skill-editor" className="sr-only">
              SKILL.md metni
            </label>
            <Textarea
              id="skill-editor"
              value={draft}
              rows={10}
              className="font-mono text-xs"
              disabled={busy}
              onChange={(event) => setDraft(event.target.value)}
            />
            <div>
              <Button type="submit" disabled={busy || !draft.trim()}>
                Öner
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function SkillRow({ skill }: { skill: SkillSummary }) {
  return (
    <li data-testid={`skill-${skill.name}`}>
      <Link
        href={`/skills/${skill.id}`}
        className={cn(
          "flex items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted",
          skill.status === "stale" && "opacity-60",
        )}
      >
        <span className="flex flex-col">
          <span className="font-medium">{skill.name}</span>
          {skill.current_version ? (
            <span className="text-xs text-muted-foreground">{skill.current_version.description}</span>
          ) : null}
        </span>
        <span className="flex items-center gap-2">
          {skill.pending_versions > 0 ? (
            <Badge variant="secondary">{skill.pending_versions} karar bekliyor</Badge>
          ) : null}
          <span className="text-xs text-muted-foreground">{skill.use_count} kullanım</span>
        </span>
      </Link>
    </li>
  );
}
