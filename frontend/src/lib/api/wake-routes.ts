"use client";

import { apiDelete, apiGetJson, apiPatchJson, apiPostJson } from "@/lib/api-client";

/**
 * Wake-phrase routes and the owner's aliases (Phase 12 D8/D20) over the
 * same-origin BFF routes (`/api/assistant/wake-routes*` -> backend
 * `/api/v1/assistant/wake-routes*`). Owner, web session only on the backend.
 * A route only reads (the digest, notices, the learning card); an alias
 * only adds a phrase -- it never widens what a voice turn may do.
 */

export interface WakeAlias {
  id: string;
  route: string;
  phrase: string;
  created_at: string;
}

export interface WakeRoute {
  name: string;
  effect: "read" | "proposal";
  phrases: string[];
  aliases: WakeAlias[];
}

export interface WakeRouteList {
  workspace: string;
  limits: { aliases_per_route: number; min_chars: number; max_chars: number };
  routes: WakeRoute[];
}

/** Friday's own words for each code-defined route. */
export const ROUTE_LABELS: Record<string, { title: string; description: string }> = {
  "digest.today": {
    title: "Bugünün brifingi",
    description: "Sabah brifingini sesli okur. Model çağrısı yok.",
  },
  "digest.learning": {
    title: "Öğrenme kartı",
    description: "Bekleyen öneri sayılarını söyler; hiçbir şeye karar vermez.",
  },
  "notices.read": {
    title: "Bildirimler",
    description: "Okunmamış bildirim başlıklarını okur; hiçbirini okundu saymaz.",
  },
};

const ws = (workspaceId: string) => `workspace=${encodeURIComponent(workspaceId)}`;

export function fetchWakeRoutes(workspaceId: string): Promise<WakeRouteList> {
  return apiGetJson(`/api/assistant/wake-routes?${ws(workspaceId)}`);
}

export function createWakeAlias(workspaceId: string, route: string, phrase: string): Promise<WakeAlias> {
  return apiPostJson(`/api/assistant/wake-routes/aliases?${ws(workspaceId)}`, { route, phrase });
}

export function updateWakeAlias(aliasId: string, phrase: string): Promise<WakeAlias> {
  return apiPatchJson(`/api/assistant/wake-routes/aliases/${encodeURIComponent(aliasId)}`, { phrase });
}

export function deleteWakeAlias(aliasId: string): Promise<void> {
  return apiDelete(`/api/assistant/wake-routes/aliases/${encodeURIComponent(aliasId)}`);
}
