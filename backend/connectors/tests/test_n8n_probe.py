"""The two branch-4 probes (Phase 12 D1/D11/D12): `connectors.n8n_finished`
and `assistant.followups_ready`. Registered at boot, notice probes, closed
links, Friday labels only, one workspace's rows only, a one-day window,
and never a dropped follow-up or a non-n8n write."""

from datetime import timedelta

import pytest
from django.utils import timezone

from approvals.models import ApprovalRequest
from assistant.models import Conversation, Message, PendingFollowUp
from assistant.probes import collect as followups_ready
from connectors.models import ConnectorWriteExecution
from connectors.probes import collect as n8n_finished
from core import probes

pytestmark = pytest.mark.django_db


def test_both_probes_are_registered_notice_probes():
    finished = probes.get_probe("connectors.n8n_finished")
    ready = probes.get_probe("assistant.followups_ready")
    assert finished.digest_section == "n8n" and finished.notice_kind == finished.name
    assert ready.digest_section == "followups" and ready.notice_kind == ready.name


def _write(workspace, owner, *, connector="n8n", status="succeeded", finished=None):
    request = ApprovalRequest.objects.create(
        workspace=workspace,
        tool_name=f"mcp_{connector}_x",
        payload={},
        requested_by=owner,
        status=ApprovalRequest.Status.APPROVED,
    )
    return ConnectorWriteExecution.objects.create(
        workspace=workspace,
        approval_request=request,
        connector=connector,
        tool="n8n_home_backup",
        status=status,
        finished_at=finished or timezone.now(),
        result={"text": "RESULT-TEXT-CANARY"},
    )


def test_n8n_finished_reads_one_workspace_and_only_n8n(owner, workspace, firm):
    now = timezone.now()
    mine = _write(workspace, owner)
    _write(firm, owner)
    _write(workspace, owner, connector="github")
    _write(workspace, owner, status="running")
    _write(workspace, owner, finished=now - timedelta(days=2))
    result = probes.validate_result(
        probes.get_probe("connectors.n8n_finished"),
        n8n_finished(workspace, owner, now),
        max_keys=200,
    )
    assert result.count == 1
    assert result.all_keys() == (str(mine.pk),)
    assert result.items[0].label == "n8n: Ev yedeği bitti"
    assert "RESULT-TEXT-CANARY" not in str(result)


def _followup(workspace, owner, conversation, *, status, message=True):
    source = Message.objects.create(
        workspace=workspace, conversation=conversation, role="user", content="sunucu durumu"
    )
    reply = None
    if message:
        reply = Message.objects.create(
            workspace=workspace,
            conversation=conversation,
            role="assistant",
            content="RESULT-TEXT-CANARY",
            followup_of=source,
        )
    return PendingFollowUp.objects.create(
        workspace=workspace,
        conversation=conversation,
        source_message=source,
        followup_message=reply,
        created_by=owner,
        channel="web",
        tool_name="n8n_server_status",
        tool_label="Sunucu durumu",
        data_class="personal",
        handle_id=str(source.pk),
        status=status,
        finished_at=timezone.now(),
    )


def test_followups_ready_reads_one_workspace_and_never_a_dropped_one(owner, workspace, firm):
    mine = Conversation.objects.create(workspace=workspace, created_by=owner)
    theirs = Conversation.objects.create(workspace=firm, created_by=owner)
    done = _followup(workspace, owner, mine, status=PendingFollowUp.Status.DONE)
    _followup(workspace, owner, mine, status=PendingFollowUp.Status.DROPPED, message=False)
    _followup(workspace, owner, mine, status=PendingFollowUp.Status.PENDING, message=False)
    _followup(firm, owner, theirs, status=PendingFollowUp.Status.DONE)
    result = probes.validate_result(
        probes.get_probe("assistant.followups_ready"),
        followups_ready(workspace, owner, timezone.now()),
        max_keys=200,
    )
    assert result.count == 1
    assert result.all_keys() == (str(done.pk),)
    assert result.items[0].label == "Sunucu durumu"
    assert result.items[0].link == f"/chat/{mine.pk}"
    assert "RESULT-TEXT-CANARY" not in str(result)
