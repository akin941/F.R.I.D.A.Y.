"use client";

import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { errorMessage } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";

/**
 * The confirmation step in front of every destructive action (Phase 4
 * branch 5): an AlertDialog naming the object and the consequence. The
 * mutation (`onConfirm`) only ever runs from the dialog's own confirm
 * button -- never from the trigger -- and the dialog stays open until it
 * settles: a failure is shown inside the dialog, success closes it.
 *
 * Uncontrolled with a `trigger` (focus returns to it on close), or
 * controlled with `open`/`onOpenChange` when the action is opened from
 * elsewhere.
 */
export function ConfirmDialog({
  trigger,
  open: controlledOpen,
  onOpenChange,
  title,
  description,
  children,
  confirmLabel,
  busyLabel,
  destructive = true,
  errorFallback,
  onConfirm,
}: {
  trigger?: React.ReactElement;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  children?: React.ReactNode;
  confirmLabel: string;
  busyLabel?: string;
  destructive?: boolean;
  errorFallback: string;
  onConfirm: () => Promise<unknown>;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = controlledOpen ?? uncontrolledOpen;

  function setOpen(next: boolean) {
    if (busy) return;
    if (!next) setError(null);
    setUncontrolledOpen(next);
    onOpenChange?.(next);
  }

  async function handleConfirm(event: React.MouseEvent) {
    // Radix closes the dialog on Action by default; keep it open until the
    // mutation settles so a failure is reported here.
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      setBusy(false);
      setUncontrolledOpen(false);
      onOpenChange?.(false);
    } catch (err) {
      setError(errorMessage(err, errorFallback));
      setBusy(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      {trigger ? <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger> : null}
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        {children}
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>{tr.common.cancel}</AlertDialogCancel>
          <AlertDialogAction
            variant={destructive ? "destructive" : "default"}
            onClick={(event) => void handleConfirm(event)}
            disabled={busy}
          >
            {busy && busyLabel ? busyLabel : confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
