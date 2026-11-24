"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { HarnessRepoPicker } from "@/components/harness/HarnessRepoPicker";
import { HARNESS_GOAL_MAX_LENGTH } from "@/components/harness/HarnessJobGoalForm";
import { apiPostJson, errorMessage } from "@/lib/api-client";
import { fetchSkills, type SkillSummary } from "@/lib/api/skills";
import type { ApiImplementationJob } from "@/types/api";

export interface ImplementationPrefill {
  repo: string;
  goal: string;
  discoveryJobId: string;
}

/**
 * "Uygulama iste": creates an implementation job in `pending_gate_a`. The
 * implementing agent (Claude Code) and the reviewer (Codex CLI) are fixed
 * server-side -- shown here, never selectable. Nothing touches the repo
 * until the owner approves the plan (Gate A); nothing is pushed until the
 * owner approves the finished, tested, reviewed change (Gate B).
 *
 * `prefill` (from a discovery job's detail view) seeds the repo + goal and
 * links the new job to that discovery job via `discovery_job`.
 */
export function ImplementationJobForm({
  workspaceId,
  prefill,
  onCreated,
  onCancel,
}: {
  workspaceId: string;
  prefill?: ImplementationPrefill | null;
  onCreated: (job: ApiImplementationJob) => void;
  onCancel: () => void;
}) {
  const [repo, setRepo] = useState(prefill?.repo ?? "");
  const [goal, setGoal] = useState(prefill?.goal ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Phase 12 D17: owner-approved instruction skills the agent may read
  // (mounted read-only); validated server-side, listed on Gate A.
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchSkills(workspaceId)
      .then((list) => {
        if (!cancelled) {
          setSkills(
            list.skills.filter(
              (skill) =>
                (skill.status === "active" || skill.status === "stale") &&
                skill.current_version !== null,
            ),
          );
        }
      })
      .catch(() => {
        // No skills page access (or none yet): the picker stays empty.
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  function toggleSkill(versionId: string) {
    setSelectedSkills((current) =>
      current.includes(versionId)
        ? current.filter((id) => id !== versionId)
        : [...current, versionId],
    );
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const job = await apiPostJson<ApiImplementationJob>("/api/harness/implementation-jobs", {
        workspace: workspaceId,
        repo,
        goal,
        ...(prefill?.discoveryJobId ? { discovery_job: prefill.discoveryJobId } : {}),
        ...(selectedSkills.length > 0 ? { skill_versions: selectedSkills } : {}),
      });
      onCreated(job);
    } catch (err) {
      setError(errorMessage(err, "Uygulama işi oluşturulamadı."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Uygulama iste</CardTitle>
      </CardHeader>
      <form onSubmit={handleSubmit}>
        <CardContent className="flex flex-col gap-3">
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <Alert>
            <AlertDescription className="flex flex-col gap-1">
              <span>
                Uygulama iki ayrı sahip onayı gerektirir: önce <strong>plan onayı (Kapı A)</strong>,
                ajan çalışmaya başlamadan; sonra değişiklikler test edilip incelendikten sonra{" "}
                <strong>gönderim onayı (Kapı B)</strong>, dal uzak depoya gönderilmeden. Her iki
                karar da Onaylar sayfasından verilir.
              </span>
              <span>
                Uygulayıcı: Claude Code · İnceleyici: Codex CLI (sabit; seçilemez).
              </span>
            </AlertDescription>
          </Alert>
          {prefill?.discoveryJobId ? (
            <p className="text-sm text-muted-foreground">
              Bir keşif işinden oluşturuluyor — depo ve hedef o işten dolduruldu.
            </p>
          ) : null}
          <HarnessRepoPicker
            id="impl-job-repo"
            workspaceId={workspaceId}
            value={repo}
            onChange={setRepo}
            requireTestCommand
            disabled={submitting}
          />
          <div className="flex flex-col gap-2">
            <Label htmlFor="impl-job-goal">Uygulama hedefi</Label>
            <Textarea
              id="impl-job-goal"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="Ajan bu depoda neyi değiştirsin?"
              rows={4}
              maxLength={HARNESS_GOAL_MAX_LENGTH}
              required
            />
            <p className="text-xs text-muted-foreground">
              {goal.length}/{HARNESS_GOAL_MAX_LENGTH}
            </p>
          </div>
          {skills.length > 0 ? (
            <fieldset className="flex flex-col gap-1" data-testid="impl-job-skills">
              <legend className="text-sm font-medium">Beceriler (isteğe bağlı)</legend>
              <p className="text-xs text-muted-foreground">
                Onayladığın beceriler ajana salt okunur talimat olarak verilir; hiçbir yetki
                tanımaz ve Kapı A&apos;da adı ve özetiyle listelenir.
              </p>
              {skills.map((skill) => {
                const versionId = skill.current_version!.id;
                return (
                  <label key={skill.id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={selectedSkills.includes(versionId)}
                      onChange={() => toggleSkill(versionId)}
                      disabled={submitting}
                    />
                    {skill.name}
                    <span className="text-xs text-muted-foreground">
                      sürüm {skill.current_version!.version}
                    </span>
                  </label>
                );
              })}
            </fieldset>
          ) : null}
        </CardContent>
        <CardFooter className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
            İptal
          </Button>
          <Button type="submit" disabled={submitting || !repo || !goal.trim()}>
            {submitting ? "Gönderiliyor…" : "Uygulama iste"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
