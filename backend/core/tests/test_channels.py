"""core.channels unit tests — no database, no kernel-app imports (mirrors
test_scaffold.py's own "core tests must not import kernel apps" rule): a
fake queryset stands in for a real WorkspaceScopedQuerySet."""

from types import SimpleNamespace

from core.channels import (
    CHANNEL_CLI,
    CHANNEL_WEB,
    ChannelPrincipal,
    is_low_trust,
    request_channel,
    scoped_for_actor,
)


class _FakeQuerySet:
    """Records the (actor, workspace) it was called with."""

    def __init__(self):
        self.calls = []

    def for_actor(self, actor, workspace=None):
        self.calls.append((actor, workspace))
        return self


def _web_request(user):
    return SimpleNamespace(user=user, auth=SimpleNamespace())  # not a ChannelPrincipal


def _channel_request(user, *, bound_workspace_id):
    return SimpleNamespace(
        user=user,
        auth=ChannelPrincipal(channel=CHANNEL_CLI, bound_workspace_id=bound_workspace_id),
    )


def test_request_channel_defaults_to_web_when_auth_is_not_a_channel_principal():
    request = _web_request(user="u1")
    assert request_channel(request) == CHANNEL_WEB
    assert is_low_trust(request) is False


def test_request_channel_reports_channel_principal():
    request = _channel_request(user="u1", bound_workspace_id="w1")
    assert request_channel(request) == CHANNEL_CLI
    assert is_low_trust(request) is True


def test_request_with_no_auth_attribute_at_all_is_web():
    request = SimpleNamespace(user="u1")
    assert request_channel(request) == CHANNEL_WEB
    assert is_low_trust(request) is False


def test_scoped_for_actor_web_request_passes_workspace_through_unchanged():
    # Query-param case.
    request = _web_request(user="u1")
    qs = _FakeQuerySet()
    scoped_for_actor(request, qs, workspace="w1")
    assert qs.calls == [("u1", "w1")]

    # By-id lookup case (no workspace parameter at all).
    qs2 = _FakeQuerySet()
    scoped_for_actor(request, qs2, workspace=None)
    assert qs2.calls == [("u1", None)]


def test_scoped_for_actor_channel_bound_request_forces_bound_workspace_on_query_param():
    request = _channel_request(user="u1", bound_workspace_id="bound-ws")
    qs = _FakeQuerySet()
    scoped_for_actor(request, qs, workspace="different-ws")
    assert qs.calls == [("u1", "bound-ws")]


def test_scoped_for_actor_channel_bound_request_forces_bound_workspace_on_by_id_lookup():
    # Proves the round-2 P1 fix: a by-id lookup (no workspace kwarg passed
    # by the view at all, i.e. the default None) still gets forced to the
    # bound workspace, not left as an unscoped "any workspace the user is a
    # member of" lookup.
    request = _channel_request(user="u1", bound_workspace_id="bound-ws")
    qs = _FakeQuerySet()
    scoped_for_actor(request, qs)
    assert qs.calls == [("u1", "bound-ws")]


def test_scoped_for_actor_channel_bound_request_forces_bound_workspace_on_body_field():
    # Body-supplied `workspace` mismatch case (a create view).
    request = _channel_request(user="u1", bound_workspace_id="bound-ws")
    qs = _FakeQuerySet()
    scoped_for_actor(request, qs, workspace="body-supplied-ws")
    assert qs.calls == [("u1", "bound-ws")]
