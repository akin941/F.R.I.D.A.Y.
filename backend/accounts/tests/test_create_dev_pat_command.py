import io

import pytest
from django.core.management import CommandError, call_command

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture(autouse=True)
def _debug_settings(settings):
    # Django's test framework forces settings.DEBUG = False for the whole
    # session (django.test.utils.setup_test_environment), regardless of
    # what config.settings.dev sets it to -- this command's own DEBUG gate
    # (round 3 fix, below) would otherwise make every other test in this
    # file fail, since command.handle() reads the real, test-forced value.
    # The one negative test that actually exercises the gate overrides
    # this back to False itself.
    settings.DEBUG = True


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def run(*args):
    out = io.StringIO()
    call_command("create_dev_pat", *args, stdout=out)
    return out.getvalue().strip()


def test_mints_a_token_bound_to_the_named_workspace(owner, workspace):
    raw_token = run(owner.email, workspace.name)

    assert raw_token.startswith(TOKEN_PREFIX)
    token = PersonalAccessToken.objects.get(token_hash=hash_token(raw_token))
    assert token.user == owner
    assert token.workspace == workspace
    assert token.revoked_at is None


def test_refuses_to_run_outside_debug(owner, workspace, settings):
    """A dev-only command that never actually checked DEBUG could mint a
    real bearer credential against a production deploy by accident (Codex
    review, branch 2 round 3)."""
    settings.DEBUG = False
    with pytest.raises(CommandError):
        run(owner.email, workspace.name)
    assert not PersonalAccessToken.objects.exists()


def test_unknown_email_raises_command_error(workspace):
    with pytest.raises(CommandError):
        run("nobody@test.dev", workspace.name)
    assert not PersonalAccessToken.objects.exists()


def test_user_not_a_member_of_named_workspace_raises_command_error(owner):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    other_workspace = Workspace.objects.get(owner=stranger)

    with pytest.raises(CommandError):
        run(owner.email, other_workspace.name)
    assert not PersonalAccessToken.objects.exists()


def test_picks_the_workspace_the_user_actually_belongs_to_when_names_collide(owner, workspace):
    """Workspace names are not globally unique -- a same-named workspace
    the user does NOT belong to must never be matched instead."""
    stranger = register_user(email="stranger2@test.dev", password=PASSWORD, name="Stranger2")
    same_name_workspace = Workspace.objects.get(owner=stranger)
    same_name_workspace.name = workspace.name
    same_name_workspace.save(update_fields=["name"])

    raw_token = run(owner.email, workspace.name)
    token = PersonalAccessToken.objects.get(token_hash=hash_token(raw_token))
    assert token.workspace == workspace


def test_ambiguous_same_named_workspaces_for_the_same_user_raises_command_error(owner, workspace):
    """The user themself belongs to two DIFFERENT workspaces sharing the
    same name -- silently picking one via `.first()` would bind the token
    to whichever row the database happened to return first (Codex review,
    branch 2 round 1). Must fail loud instead of guessing."""
    second_workspace = Workspace.objects.create(name=workspace.name, owner=owner)
    WorkspaceMember.objects.create(
        workspace=second_workspace, user=owner, role=WorkspaceMember.Role.OWNER
    )

    with pytest.raises(CommandError):
        run(owner.email, workspace.name)
    assert not PersonalAccessToken.objects.exists()


def test_member_of_a_shared_workspace_can_mint_their_own_token(owner, workspace):
    member = register_user(email="member@test.dev", password=PASSWORD, name="Member")
    WorkspaceMember.objects.create(
        workspace=workspace, user=member, role=WorkspaceMember.Role.MEMBER
    )

    raw_token = run(member.email, workspace.name)
    token = PersonalAccessToken.objects.get(token_hash=hash_token(raw_token))
    assert token.user == member
    assert token.workspace == workspace
