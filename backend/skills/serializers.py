"""Payloads of the `/skills` surface (Phase 12 D20). Owner-only views, so
the owner sees the full text of every version (that is what they approve)."""

from rest_framework import serializers

from skills.models import InstructionSkill, InstructionSkillVersion
from skills.skill_md import render_skill_md

TEXT_MAX_CHARS = 200_000


class SkillTextSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=TEXT_MAX_CHARS, trim_whitespace=False)


class DecisionSerializer(serializers.Serializer):
    sha256 = serializers.RegexField(r"^[0-9a-f]{64}$")


class RejectSerializer(serializers.Serializer):
    sha256 = serializers.RegexField(r"^[0-9a-f]{64}$", required=False)


class EditApproveSerializer(serializers.Serializer):
    sha256 = serializers.RegexField(r"^[0-9a-f]{64}$")
    text = serializers.CharField(max_length=TEXT_MAX_CHARS, trim_whitespace=False)


def version_summary(version):
    if version is None:
        return None
    return {
        "id": str(version.pk),
        "version": version.version,
        "sha256": version.content_sha256,
        "decision": version.decision,
        "proposed_by": version.proposed_by,
        "description": version.description,
        "created_at": version.created_at.isoformat(),
        "decided_at": version.decided_at.isoformat() if version.decided_at else None,
    }


def version_payload(version):
    return {
        **version_summary(version),
        "skill_id": str(version.skill_id),
        "front_matter": version.front_matter,
        "body": version.body,
        "references": version.references,
        "text": render_skill_md(version.front_matter, version.body),
        "provenance": version.provenance,
    }


def skill_summary(skill, *, pending=0):
    return {
        "id": str(skill.pk),
        "workspace": str(skill.workspace_id),
        "name": skill.name,
        "status": skill.status,
        "origin": skill.origin,
        "use_count": skill.use_count,
        "last_used_at": skill.last_used_at.isoformat() if skill.last_used_at else None,
        "current_version": version_summary(skill.current_version),
        "pending_versions": pending,
        "created_at": skill.created_at.isoformat(),
    }


def skill_detail(skill):
    versions = InstructionSkillVersion.objects.filter(skill=skill).order_by("-version")
    pending = sum(1 for v in versions if v.decision == InstructionSkillVersion.Decision.PENDING)
    return {
        **skill_summary(skill, pending=pending),
        "versions": [version_payload(version) for version in versions],
    }


STATUSES = [choice for choice, _ in InstructionSkill.Status.choices]
