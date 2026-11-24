"use client";

import { apiGetJson, apiPostForm, apiPostJson } from "@/lib/api-client";

/**
 * SKILL.md instruction skills (Phase 12 D13/D14/D20) over the same-origin
 * BFF routes (`/api/skills*` -> backend `/api/v1/skills*`). Owner, web
 * session only on the backend. A skill is text: approving one changes what
 * Friday is told for a kind of request, never which tools exist or what
 * needs approval. Every version is approved by the hash the owner was
 * shown; there is no bulk approve and no delete (archive instead).
 */

export type SkillStatus = "proposed" | "active" | "stale" | "rejected" | "archived";
export type VersionDecision = "pending" | "approved" | "rejected" | "superseded";
export type ProposedBy = "owner" | "curator" | "playbook";

export interface SkillVersionSummary {
  id: string;
  version: number;
  sha256: string;
  decision: VersionDecision;
  proposed_by: ProposedBy;
  description: string;
  created_at: string;
  decided_at: string | null;
}

export interface SkillVersion extends SkillVersionSummary {
  skill_id: string;
  front_matter: Record<string, unknown>;
  body: string;
  references: Record<string, string>;
  text: string;
  provenance: Record<string, unknown>;
}

export interface SkillSummary {
  id: string;
  workspace: string;
  name: string;
  status: SkillStatus;
  origin: "owner" | "upload" | "curator" | "playbook";
  use_count: number;
  last_used_at: string | null;
  current_version: SkillVersionSummary | null;
  pending_versions: number;
  created_at: string;
}

export interface SkillDetail extends SkillSummary {
  versions: SkillVersion[];
}

export interface SkillList {
  workspace: string;
  limits: { max_active: number; body_max_chars: number; bundle_max_bytes: number };
  skills: SkillSummary[];
}

export const STATUS_LABELS: Record<SkillStatus, string> = {
  proposed: "Önerildi",
  active: "Etkin",
  stale: "Kullanılmıyor",
  rejected: "Reddedildi",
  archived: "Arşivde",
};

export const DECISION_LABELS: Record<VersionDecision, string> = {
  pending: "Karar bekliyor",
  approved: "Onaylandı",
  rejected: "Reddedildi",
  superseded: "Yerine yenisi geldi",
};

export const PROPOSER_LABELS: Record<ProposedBy, string> = {
  owner: "Sen",
  curator: "Küratör önerisi",
  playbook: "Harness oyun kitabı önerisi",
};

const ws = (workspaceId: string) => `workspace=${encodeURIComponent(workspaceId)}`;
const id = (value: string) => encodeURIComponent(value);

export function fetchSkills(workspaceId: string): Promise<SkillList> {
  return apiGetJson(`/api/skills?${ws(workspaceId)}`);
}

export function fetchSkill(skillId: string): Promise<SkillDetail> {
  return apiGetJson(`/api/skills/${id(skillId)}`);
}

export function createSkillFromText(workspaceId: string, text: string): Promise<SkillVersion> {
  return apiPostJson(`/api/skills?${ws(workspaceId)}`, { text });
}

export function uploadSkill(workspaceId: string, file: File): Promise<SkillVersion> {
  const form = new FormData();
  form.set("file", file, file.name);
  return apiPostForm(`/api/skills/upload?${ws(workspaceId)}`, form);
}

export function proposeVersion(skillId: string, text: string): Promise<SkillVersion> {
  return apiPostJson(`/api/skills/${id(skillId)}/versions`, { text });
}

export function approveVersion(versionId: string, sha256: string): Promise<SkillVersion> {
  return apiPostJson(`/api/skills/versions/${id(versionId)}/approve`, { sha256 });
}

export function editAndApprove(versionId: string, sha256: string, text: string): Promise<SkillVersion> {
  return apiPostJson(`/api/skills/versions/${id(versionId)}/edit-approve`, { sha256, text });
}

export function rejectVersion(versionId: string, sha256: string): Promise<SkillVersion> {
  return apiPostJson(`/api/skills/versions/${id(versionId)}/reject`, { sha256 });
}

export function archiveSkill(skillId: string): Promise<SkillDetail> {
  return apiPostJson(`/api/skills/${id(skillId)}/archive`);
}

export function restoreSkill(skillId: string): Promise<SkillDetail> {
  return apiPostJson(`/api/skills/${id(skillId)}/restore`);
}

/**
 * The weekly curator (Phase 12 D16): stale marks, at most three proposals
 * (pending versions, decided on each skill's page) and archive suggestions
 * the owner archives or dismisses here. The curator never applies anything.
 */
export type ArchiveReason = "unused" | "duplicate" | "outdated" | "merged";

export interface CuratorRun {
  id: string;
  week: string;
  outcome: string;
  started_at: string;
  finished_at: string | null;
  stale_marked: number;
  reactivated: number;
  skills_considered: number;
  proposals_created: number;
  proposals_dropped: number;
  drop_reasons: Record<string, number>;
  proposal_version_ids: string[];
}

export interface ArchiveSuggestion {
  run_id: string;
  skill_id: string;
  skill_name: string;
  skill_status: SkillStatus;
  use_count: number;
  last_used_at: string | null;
  reason: ArchiveReason;
  into_skill_id: string | null;
  into_skill_name: string | null;
}

export interface CuratorState {
  workspace: string;
  latest_run: CuratorRun | null;
  suggestions: ArchiveSuggestion[];
}

export const ARCHIVE_REASON_LABELS: Record<ArchiveReason, string> = {
  unused: "Uzun süredir kullanılmıyor",
  duplicate: "Başka bir beceriyle aynı işi yapıyor",
  outdated: "Güncelliğini yitirmiş",
  merged: "Başka bir beceriyle birleştirilmesi önerildi",
};

export const CURATOR_ACTION_LABELS: Record<string, string> = {
  edit: "düzenleme",
  merge: "birleştirme",
};

export function fetchCurator(workspaceId: string): Promise<CuratorState> {
  return apiGetJson(`/api/skills/curator?${ws(workspaceId)}`);
}

export function dismissSuggestion(runId: string, skillId: string): Promise<unknown> {
  return apiPostJson(`/api/skills/curator/dismiss`, { run_id: runId, skill_id: skillId });
}

/** A curator version's provenance, in Friday's words (no model text). */
export function curatorNote(provenance: Record<string, unknown> | null | undefined): string | null {
  if (!provenance || typeof provenance.curator_run_id !== "string") return null;
  const action = typeof provenance.action === "string" ? CURATOR_ACTION_LABELS[provenance.action] : undefined;
  const week = typeof provenance.week === "string" ? provenance.week : "";
  return [action ? `Haftalık küratör ${action} önerisi` : "Haftalık küratör önerisi", week].filter(Boolean).join(" · ");
}
