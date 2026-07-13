"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Textarea } from "@/components/ui/textarea";
import { apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiAgent } from "@/types/api";

/**
 * Plain-text goal -> `POST` creates the `Agent` and triggers planning
 * (brief §5). No inline plan editing in this MVP -- if the plan looks
 * wrong once it appears, cancel and re-submit a clearer goal.
 */
export function AgentGoalForm({
  workspaceId,
  onCreated,
  onCancel,
}: {
  workspaceId: string;
  onCreated: (agent: ApiAgent) => void;
  onCancel: () => void;
}) {
  const [goal, setGoal] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const agent = await apiPostJson<ApiAgent>("/api/agents", {
        workspace: workspaceId,
        goal,
      });
      onCreated(agent);
      setGoal("");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "That agent could not be started.")
          : "Something went wrong. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>New agent</CardTitle>
      </CardHeader>
      <form onSubmit={handleSubmit}>
        <CardContent className="flex flex-col gap-3">
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <Textarea
            aria-label="Goal"
            placeholder="What should the agent do?"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            rows={3}
            required
          />
        </CardContent>
        <CardFooter className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" disabled={submitting || !goal.trim()}>
            {submitting ? "Planning…" : "Start agent"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
