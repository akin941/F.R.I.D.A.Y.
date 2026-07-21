"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { APPROVAL_STATUS_LABELS, EXECUTION_STATUS_LABELS } from "@/components/approvals/labels";
import { apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiApprovalRequest } from "@/types/api";

export function ApprovalCard({
  request,
  canDecide,
  onChanged,
}: {
  request: ApiApprovalRequest;
  canDecide: boolean;
  onChanged: (request: ApiApprovalRequest) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ADR-008 decision 3 (single-owner): the gate is an explicit, separate,
  // audited decision over the high-trust web channel -- the owner decides
  // their own and their agents' requests, so no own-request hiding here.
  // `canDecide` reflects the workspace role check, enforced server-side
  // unconditionally; hiding for members/viewers is a UI-level reflection
  // of that rule, not a substitute for it.

  async function handleDecide(action: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      const updated = await apiPostJson<ApiApprovalRequest>(
        `/api/approvals/requests/${request.id}/${action}`,
      );
      onChanged(updated);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.body.detail ?? "İşlem tamamlanamadı.")
          : "Bir şeyler ters gitti. Lütfen tekrar deneyin.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{request.tool_name}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{APPROVAL_STATUS_LABELS[request.status]}</span>
          {request.decided_at ? (
            <>
              <span>&middot;</span>
              <span>Karara bağlandı {new Date(request.decided_at).toLocaleString()}</span>
            </>
          ) : null}
        </div>
        {/* Approval and actual completion are two separate async moments
            (dispatch is async, brief §2) -- this must never imply
            "approved" means "done". */}
        {request.execution ? (
          <p className="text-sm">
            Çalışma durumu:{" "}
            {EXECUTION_STATUS_LABELS[request.execution.status] ?? request.execution.status}
            {request.execution.status === "failed" && request.execution.error_message
              ? ` — ${request.execution.error_message}`
              : null}
            {request.execution.status === "succeeded"
              ? ` (${request.execution.written_count} yazıldı, ${request.execution.updated_count} güncellendi, ${request.execution.removed_count} silindi)`
              : null}
          </p>
        ) : null}
      </CardContent>
      {request.status === "pending" && canDecide ? (
        <CardFooter className="flex justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleDecide("reject")}
            disabled={busy}
          >Reddet</Button>
          <Button size="sm" onClick={() => void handleDecide("approve")} disabled={busy}>Onayla</Button>
        </CardFooter>
      ) : null}
    </Card>
  );
}
