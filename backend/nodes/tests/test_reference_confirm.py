"""The confirmation-window double (Phase 10 brief D5 check 23)."""

import pytest

from nodes.capabilities import get_action_contract
from nodes.reference.confirm import (
    ConfirmationWindowDouble,
    Decision,
    FakeClock,
    WindowBusy,
    render_confirmation,
)
from nodes.reference.local_policy import CommandMapping, RepoMapping, Resolved


def test_no_decision_times_out_after_exactly_60_seconds():
    clock = FakeClock(1000)
    window = ConfirmationWindowDouble(clock)
    window.open("inv-1", "text")
    clock.advance(59)
    assert window.poll() is None and window.is_open
    clock.advance(1)
    assert window.poll() == "timed_out"
    assert not window.is_open


@pytest.mark.parametrize(("action", "state"), [("approve", "approved"), ("deny", "denied"),
                                               ("close", "closed")])
def test_scripted_decisions_take_effect_after_their_delay(action, state):
    clock = FakeClock(0)
    window = ConfirmationWindowDouble(clock, [Decision(action, 10)])
    window.open("inv", "text")
    clock.advance(9)
    assert window.poll() is None
    clock.advance(1)
    assert window.poll() == state


def test_a_decision_after_the_timeout_never_counts():
    clock = FakeClock(0)
    window = ConfirmationWindowDouble(clock, [Decision("approve", 61)])
    window.open("inv", "text")
    clock.advance(61)
    assert window.poll() == "timed_out"


def test_one_window_at_a_time():
    window = ConfirmationWindowDouble(FakeClock(0))
    window.open("a", "text")
    with pytest.raises(WindowBusy):
        window.open("b", "text")
    assert window.invocation_id == "a"


def test_disconnect_closes_the_window_with_no_decision():
    clock = FakeClock(0)
    window = ConfirmationWindowDouble(clock, [Decision("approve", 5)])
    window.open("a", "text")
    window.force_close("disconnect")
    clock.advance(10)
    assert window.poll() is None
    assert not window.is_open
    assert window.closed_by == ["disconnect"]


def test_bad_decisions_are_refused():
    with pytest.raises(ValueError):
        Decision("run_anyway")
    with pytest.raises(ValueError):
        Decision("approve", -1)


def test_the_text_is_rendered_by_the_node_from_its_own_args():
    contract = get_action_contract("repo.command.run")
    repo = RepoMapping("acme", "~/work/acme", "/home/o/work/acme")
    command = CommandMapping(kind="test", argv=("npm", "test"), network_domains=("npmjs.org",))
    text = render_confirmation(
        contract,
        {"repo_path": "~/work/acme", "command_kind": "test"},
        approval_id="8d7c6b5a-4e3f-4d2c-8b1a-0f9e8d7c6b5a",
        resolved=Resolved("repo.command.run", repo=repo, command=command),
    )
    assert text.splitlines() == [
        "~/work/acme reposunda 'test' komutu çalıştırılsın mı?",
        "Repo: acme",
        "Komut: npm test",
        "Ağ: npmjs.org",
        "Onay: 8d7c6b5a",
    ]


def test_a_recipe_window_uses_the_recipe_template_and_label():
    contract = get_action_contract("applescript.recipe.run")
    args = {"recipe": "system.volume_set", "params": {"level": 30}}
    text = render_confirmation(contract, args, recipe=contract.recipe_for(args))
    assert "30" in text
    assert "Tarif:" in text
    assert "Onay:" not in text  # no approval id without a grant
