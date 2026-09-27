"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { HarnessRepoPicker } from "@/components/harness/HarnessRepoPicker";
import { HARNESS_GOAL_MAX_LENGTH } from "@/components/harness/HarnessJobGoalForm";
import { apiPostJson, ApiError } from "@/lib/api-client";
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
      });
      onCreated(job);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "Uygulama işi oluşturulamadı.")
          : "Bir şeyler ters gitti. Lütfen tekrar deneyin.",
      );
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
