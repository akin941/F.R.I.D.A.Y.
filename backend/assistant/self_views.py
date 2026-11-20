"""`GET /api/v1/ai/self-sources` (Phase 8A brief branch 6): the source
anchors of the read-only `self` core block, for the core-block editor.

The `self` block is assembled deterministically from the committed snapshot
(`assistant/self_sources.json`, D8); this view lists that snapshot's quoted
lines with their `file:line` anchors so the owner can see where every
sentence comes from. Owner, trusted web session only (the same rule as the
core-block API, which lives in `memory` and may not import `assistant`);
default authentication, so a PAT is refused by authentication itself. The
snapshot is public repository text: nothing workspace-scoped is read.
"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assistant.self_sources import load_snapshot
from core.permissions import IsOwner, IsWebChannel


def _anchor(group, entry, *, label=""):
    return {
        "group": group,
        "label": label,
        "file": entry.get("file", ""),
        "line": entry.get("line"),
        "text": entry.get("text", ""),
    }


def snapshot_anchors(snapshot):
    """The snapshot as a flat, ordered anchor list: identity, rules, each
    accepted ADR's status and decision lines, the current phase row."""
    if not snapshot:
        return []
    anchors = []
    identity = snapshot.get("identity")
    if identity:
        anchors.append(_anchor("identity", identity))
    for rule in snapshot.get("rules") or []:
        anchors.append(_anchor("rule", rule))
    for adr in snapshot.get("adrs") or []:
        label = f"{adr.get('id', '')}: {adr.get('title', '')}".strip(": ")
        decision = adr.get("decision") or {}
        anchors.append(
            _anchor(
                "adr",
                {"file": adr.get("file", ""), "line": decision.get("line"), **decision},
                label=label,
            )
        )
    phase = snapshot.get("phase")
    if phase:
        anchors.append(
            _anchor(
                "phase",
                {
                    "file": phase.get("file", ""),
                    "line": phase.get("line"),
                    "text": f"{phase.get('phase', '')} — {phase.get('status', '')}".strip(" —"),
                },
            )
        )
    return anchors


class SelfSourcesView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel, IsOwner]

    def get(self, request):
        snapshot = load_snapshot()
        return Response(
            {
                "content_hash": (snapshot or {}).get("content_hash", ""),
                "anchors": snapshot_anchors(snapshot),
            }
        )
