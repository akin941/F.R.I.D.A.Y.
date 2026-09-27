"use client";

import { useState } from "react";
import { Lock, MessagesSquare, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ChannelBadge, PrivateBadge } from "@/components/chat/badges";
import type { ConversationPatch } from "@/lib/query/chat";
import { errorMessage } from "@/lib/api-client";
import { tr } from "@/lib/i18n/tr";
import type { ApiConversation } from "@/types/api";

/**
 * The chat column's header: the phone drawer trigger (below `md`), the
 * conversation's title and badges, and its management actions -- rename,
 * and mark private behind an AlertDialog (the PATCH is one-way: a private
 * conversation can never become normal again, see Conversation.private).
 */
export function ConversationHeader({
  conversation,
  onOpenList,
  onUpdate,
  actionsDisabled,
}: {
  conversation: ApiConversation | null;
  onOpenList: () => void;
  onUpdate: (patch: ConversationPatch) => Promise<unknown>;
  actionsDisabled: boolean;
}) {
  const [renameOpen, setRenameOpen] = useState(false);
  const [privateOpen, setPrivateOpen] = useState(false);

  return (
    <div className="flex min-h-12 shrink-0 items-center gap-2 border-b border-border px-2 py-1.5 md:px-4">
      <Button
        variant="ghost"
        size="sm"
        className="md:hidden"
        onClick={onOpenList}
        aria-label={tr.chat.openConversations}
      >
        <MessagesSquare aria-hidden />
        {tr.chat.conversations}
      </Button>
      <h2 className="min-w-0 flex-1 truncate text-sm font-medium" title={conversation?.title}>
        {conversation ? conversation.title || tr.chat.untitled : null}
      </h2>
      {conversation ? (
        <div className="flex shrink-0 items-center gap-1">
          <ChannelBadge channel={conversation.origin_channel} />
          {conversation.private ? <PrivateBadge /> : null}
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={tr.chat.rename}
            title={tr.chat.rename}
            onClick={() => setRenameOpen(true)}
            disabled={actionsDisabled}
          >
            <Pencil aria-hidden />
          </Button>
          {!conversation.private ? (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={tr.chat.markPrivate}
              title={tr.chat.markPrivate}
              onClick={() => setPrivateOpen(true)}
              disabled={actionsDisabled}
            >
              <Lock aria-hidden />
            </Button>
          ) : null}
          {renameOpen ? (
            <RenameDialog
              conversation={conversation}
              onClose={() => setRenameOpen(false)}
              onUpdate={onUpdate}
            />
          ) : null}
          <MarkPrivateDialog
            open={privateOpen}
            onOpenChange={setPrivateOpen}
            onUpdate={onUpdate}
          />
        </div>
      ) : null}
    </div>
  );
}

function RenameDialog({
  conversation,
  onClose,
  onUpdate,
}: {
  conversation: ApiConversation;
  onClose: () => void;
  onUpdate: (patch: ConversationPatch) => Promise<unknown>;
}) {
  const [title, setTitle] = useState(conversation.title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onUpdate({ title: title.trim() });
      onClose();
    } catch (err) {
      setError(errorMessage(err, tr.chat.renameFailed));
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(open) => (!open && !busy ? onClose() : undefined)}>
      <DialogContent>
        <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>{tr.chat.renameTitle}</DialogTitle>
            <DialogDescription>{tr.chat.renameHint}</DialogDescription>
          </DialogHeader>
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <div className="flex flex-col gap-2">
            <Label htmlFor="conversation-title">{tr.chat.renameLabel}</Label>
            <Input
              id="conversation-title"
              value={title}
              maxLength={255}
              autoFocus
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
              {tr.common.cancel}
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? tr.chat.saving : tr.chat.save}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function MarkPrivateDialog({
  open,
  onOpenChange,
  onUpdate,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onUpdate: (patch: ConversationPatch) => Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm(event: React.MouseEvent) {
    // Keep the dialog open until the PATCH lands, so a failure is shown
    // here instead of silently closing.
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onUpdate({ private: true });
      onOpenChange(false);
    } catch (err) {
      setError(errorMessage(err, tr.chat.markPrivateFailed));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return;
        if (!next) setError(null);
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{tr.chat.markPrivateTitle}</AlertDialogTitle>
          <AlertDialogDescription>{tr.chat.markPrivateDescription}</AlertDialogDescription>
        </AlertDialogHeader>
        {error ? (
          <Alert variant="destructive" role="alert">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>{tr.common.cancel}</AlertDialogCancel>
          <AlertDialogAction onClick={(e) => void confirm(e)} disabled={busy}>
            {busy ? tr.chat.markingPrivate : tr.chat.markPrivate}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
