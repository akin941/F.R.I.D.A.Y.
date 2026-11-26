"use client";

import { apiDelete, apiGetJson, apiPatchJson, apiPostJson } from "@/lib/api-client";
import type {
  ApiHomeEntity,
  ApiHomeEntityCandidates,
  ApiHomeEntityList,
  ApiNodeAuditPage,
  ApiNodeDetail,
  ApiNodeRole,
  ApiRoom,
  ApiRoomList,
} from "@/types/api";

/**
 * The owner's Home Assistant entity allowlist (Phase 13 D11). Owner, web
 * session only on the backend; an empty allowlist means no HA entity call
 * is possible. Adding an entity is always the owner's own act.
 */
const HA = "/api/connectors/home-assistant";

export function fetchHomeEntities(workspaceId: string): Promise<ApiHomeEntityList> {
  return apiGetJson(`${HA}/entities?workspace=${encodeURIComponent(workspaceId)}`);
}

export function fetchHomeEntityCandidates(workspaceId: string): Promise<ApiHomeEntityCandidates> {
  return apiGetJson(`${HA}/candidates?workspace=${encodeURIComponent(workspaceId)}`);
}

export interface HomeEntityInput {
  label?: string;
  room_id?: string | null;
  aliases?: string[];
  services?: string[];
  enabled?: boolean;
}

export function createHomeEntity(
  workspaceId: string,
  entityId: string,
  input: HomeEntityInput,
): Promise<ApiHomeEntity> {
  return apiPostJson(`${HA}/entities`, { workspace: workspaceId, entity_id: entityId, ...input });
}

export function updateHomeEntity(id: string, input: HomeEntityInput): Promise<ApiHomeEntity> {
  return apiPatchJson(`${HA}/entities/${encodeURIComponent(id)}`, input);
}

export function deleteHomeEntity(id: string): Promise<void> {
  return apiDelete(`${HA}/entities/${encodeURIComponent(id)}`);
}

/**
 * Rooms and node labels (Phase 13 D2/D3, D8) over the same-origin BFF
 * routes. Owner, web session only on the backend. Labels are server-side
 * display and resolution data: nothing here ever reaches a node's policy.
 */

const rooms = (workspaceId: string) => `/api/workspaces/${encodeURIComponent(workspaceId)}/rooms`;

export function fetchRooms(workspaceId: string): Promise<ApiRoomList> {
  return apiGetJson(rooms(workspaceId));
}

export interface RoomInput {
  name?: string;
  aliases?: string[];
  sort_order?: number;
}

export function createRoom(workspaceId: string, input: RoomInput): Promise<ApiRoom> {
  return apiPostJson(rooms(workspaceId), input);
}

export function updateRoom(workspaceId: string, roomId: string, input: RoomInput): Promise<ApiRoom> {
  return apiPatchJson(`${rooms(workspaceId)}/${encodeURIComponent(roomId)}`, input);
}

export function deleteRoom(workspaceId: string, roomId: string): Promise<void> {
  return apiDelete(`${rooms(workspaceId)}/${encodeURIComponent(roomId)}`);
}

export interface NodeLabelInput {
  name?: string;
  room_id?: string | null;
  role?: ApiNodeRole;
  aliases?: string[];
}

export function updateNodeLabels(nodeId: string, input: NodeLabelInput): Promise<ApiNodeDetail> {
  return apiPatchJson(`/api/nodes/${encodeURIComponent(nodeId)}`, input);
}

export function fetchNodeAudit(nodeId: string, before?: string | null): Promise<ApiNodeAuditPage> {
  const query = before ? `?before=${encodeURIComponent(before)}` : "";
  return apiGetJson(`/api/nodes/${encodeURIComponent(nodeId)}/audit${query}`);
}

/** Comma-separated aliases as typed in an input -> the list the API takes. */
export function parseAliases(text: string): string[] {
  return text
    .split(",")
    .map((alias) => alias.trim())
    .filter((alias) => alias.length > 0);
}
