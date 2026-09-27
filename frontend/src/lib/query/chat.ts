"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGetJson, apiPatchJson } from "@/lib/api-client";
import { queryKeys } from "@/lib/query/keys";
import type { ApiConversation, ApiMessage } from "@/types/api";

/**
 * Chat data on the query layer (Phase 4 branch 4). No polling: the list
 * and the history change only through this tab's own actions (send,
 * rename, mark private) or a focus refetch. Keys start with the workspace
 * id, so a stale response for a previous workspace or conversation lands
 * in its own cache entry and is never rendered for the current one.
 */

export function fetchConversations(workspaceId: string): Promise<ApiConversation[]> {
  return apiGetJson<ApiConversation[]>(
    `/api/conversations?workspace=${encodeURIComponent(workspaceId)}`,
  );
}

export function fetchMessages(conversationId: string): Promise<ApiMessage[]> {
  return apiGetJson<ApiMessage[]>(`/api/conversations/${conversationId}/messages`);
}

export function useConversations(workspaceId: string) {
  return useQuery({
    queryKey: queryKeys.conversations(workspaceId),
    queryFn: () => fetchConversations(workspaceId),
  });
}

export function useMessages(workspaceId: string, conversationId: string | null) {
  return useQuery({
    queryKey: queryKeys.messages(workspaceId, conversationId ?? ""),
    queryFn: () => fetchMessages(conversationId as string),
    enabled: conversationId !== null,
  });
}

export type ConversationPatch = { title?: string; private?: true };

/** Rename and/or mark private (one-way) -- `PATCH /api/conversations/<id>`. */
export function useUpdateConversation(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ConversationPatch }) =>
      apiPatchJson<ApiConversation>(`/api/conversations/${id}`, patch),
    onSuccess: (updated) => {
      queryClient.setQueryData<ApiConversation[]>(
        queryKeys.conversations(workspaceId),
        (prev) => prev?.map((c) => (c.id === updated.id ? updated : c)),
      );
    },
  });
}
