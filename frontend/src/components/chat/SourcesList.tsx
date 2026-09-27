import Link from "next/link";
import { BookOpen, Brain } from "lucide-react";
import { tr } from "@/lib/i18n/tr";

export function memoryHref(workspaceId: string, memoryId: string): string {
  return `/memory?workspace=${encodeURIComponent(workspaceId)}&memory=${encodeURIComponent(memoryId)}`;
}

export function documentHref(workspaceId: string, documentId: string): string {
  return `/knowledge?workspace=${encodeURIComponent(workspaceId)}&document=${encodeURIComponent(documentId)}`;
}

/**
 * "Kaynaklar": the memory and knowledge context supplied to a reply
 * (`memories_used`/`chunks_used` -- context, not proof of use), collapsed
 * by default, each entry deep-linking to the item. An overlay memory lives
 * in the owner's personal workspace, so its link names that workspace (the
 * shell switches to it); a chunk links to its document.
 */
export function SourcesList({
  workspaceId,
  memoryIds,
  chunkIds,
  chunkDocuments,
  overlaySourceWorkspaces,
}: {
  workspaceId: string;
  memoryIds: string[];
  chunkIds: string[];
  chunkDocuments: Record<string, string>;
  overlaySourceWorkspaces: Record<string, string>;
}) {
  if (memoryIds.length === 0 && chunkIds.length === 0) return null;
  return (
    <details data-testid="message-sources" className="group text-xs text-muted-foreground">
      <summary className="cursor-pointer rounded select-none hover:text-foreground">
        {tr.chat.sources.title} · {tr.chat.sources.summary(memoryIds.length, chunkIds.length)}
      </summary>
      <ul className="mt-1.5 flex flex-col gap-1 pl-1">
        {memoryIds.map((id, index) => {
          const overlay = overlaySourceWorkspaces[id];
          return (
            <li key={`m-${id}`} className="flex items-center gap-1.5">
              <Brain aria-hidden className="size-3.5 shrink-0" />
              <Link
                href={memoryHref(overlay ?? workspaceId, id)}
                className="text-primary underline underline-offset-4"
              >
                {tr.chat.sources.memory(index + 1)}
              </Link>
              {overlay ? <span>({tr.chat.sources.fromPersonal})</span> : null}
            </li>
          );
        })}
        {chunkIds.map((id, index) => {
          const documentId = chunkDocuments[id];
          return (
            <li key={`c-${id}`} className="flex items-center gap-1.5">
              <BookOpen aria-hidden className="size-3.5 shrink-0" />
              {documentId ? (
                <Link
                  href={documentHref(workspaceId, documentId)}
                  className="text-primary underline underline-offset-4"
                >
                  {tr.chat.sources.chunk(index + 1)}
                </Link>
              ) : (
                <span>
                  {tr.chat.sources.chunk(index + 1)} ({tr.chat.sources.noLink})
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}
