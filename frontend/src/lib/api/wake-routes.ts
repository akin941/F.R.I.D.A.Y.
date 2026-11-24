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
  /** Phase 12 branch 4: a `tool.*` route's Friday-authored tool label ("" otherwise). */
  label?: string;
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

/**
 * Title and description of any route: the code-defined ones above, a
 * `tool.*` route (Phase 12 branch 4: an owner alias for one tool, followed
 * by the request -- "ev yedeği nas"; a read tool's route reads, a write
 * tool's route only proposes a card the owner approves on screen), or a
 * `skill.<name>` route (Phase 12 branch 5: one per usable instruction skill;
 * an utterance that starts with one of its aliases uses that skill for the
 * turn, the normal router still decides). Accepts the route or its name.
 */
export function routeLabel(
  routeOrName: WakeRoute | string,
): { title: string; description: string } {
  const route = typeof routeOrName === "string" ? { name: routeOrName } : routeOrName;
  const known = ROUTE_LABELS[route.name];
  if (known) return known;
  if (route.name.startsWith("skill.")) {
    return {
      title: `Beceri: ${route.name.slice("skill.".length)}`,
      description:
        "Takma adla başlayan cümle bu beceriyi kullanır. Yalnızca talimat metnidir; hiçbir aracı açmaz.",
    };
  }
  if (route.name.startsWith("tool.")) {
    const full = route as Partial<WakeRoute> & { name: string };
    return {
      title: full.label || route.name.slice("tool.".length),
      description:
        full.effect === "proposal"
          ? "Takma adın ardından söylediklerinden bir taslak kart önerir; onay ekranda verilir."
          : "Takma adın ardından söylediklerinle bu aracı okur; yavaş okumaların sonucu konuşmaya sonradan yazılır.",
    };
  }
  return { title: route.name, description: "" };
}

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
