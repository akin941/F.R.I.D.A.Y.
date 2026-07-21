"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiHarnessJob } from "@/types/api";

/**
 * `repo` is a registered `HarnessRepo` id, entered directly -- the repo
 * allowlist itself is curator-gated registration (branch 1), managed
 * through its own API, not a picker here (brief §4: minimal API only, no
 * harness console redesign). `agent` is left unset -- Claude Code is the
 * only selectable agent this phase ships (brief), so the backend's own
 * default covers it.
 */
export function HarnessJobGoalForm({
  workspaceId,
  onCreated,
  onCancel,
}: {
  workspaceId: string;
  onCreated: (job: ApiHarnessJob) => void;
  onCancel: () => void;
}) {
  const [repo, setRepo] = useState("");
  const [goal, setGoal] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const job = await apiPostJson<ApiHarnessJob>("/api/harness/jobs", {
        workspace: workspaceId,
        repo,
        goal,
      });
      onCreated(job);
      setRepo("");
      setGoal("");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "İş başlatılamadı.")
          : "Bir şeyler ters gitti. Lütfen tekrar deneyin.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Yeni harness işi</CardTitle>
      </CardHeader>
      <form onSubmit={handleSubmit}>
        <CardContent className="flex flex-col gap-3">
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <div className="flex flex-col gap-2">
            <Label htmlFor="harness-job-repo">Depo kimliği</Label>
            <Input
              id="harness-job-repo"
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              placeholder="Kayıtlı harness deposunun UUID'si"
              required
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="harness-job-goal">Hedef</Label>
            <Textarea
              id="harness-job-goal"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="Ajan bu depoda ne bulsun?"
              rows={3}
              required
            />
          </div>
        </CardContent>
        <CardFooter className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
            İptal
          </Button>
          <Button type="submit" disabled={submitting || !repo.trim() || !goal.trim()}>
            {submitting ? "Başlatılıyor…" : "İşi başlat"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
