"""Phase 13b (ADR-018, branch 1b): the direct-media ports in
`tools.home_ports`. An unregistered, raising or misbehaving classifier is
never direct (`not_available`); no executor is registered in branch 1b,
so `run_home_direct` answers `not_direct` and sends nothing; an executor
that raises is `error` (never a card). The verdict and run types refuse
inconsistent values."""

import pytest

from tools import home_ports
from tools.home_ports import DirectRun, DirectVerdict

KWARGS = {
    "workspace": None,
    "actor": None,
    "arguments": {},
    "origin": "chat",
    "channel": "web",
    "input_mode": "text",
}


@pytest.fixture
def restore_ports():
    classifier = home_ports.direct_classifier()
    executor = home_ports.direct_executor()
    yield
    home_ports.unregister_direct_classifier()
    home_ports.unregister_direct_executor()
    if classifier is not None:
        home_ports.register_direct_classifier(classifier)
    if executor is not None:
        home_ports.register_direct_executor(executor)


def test_the_boot_registers_the_classifier_and_no_executor():
    # Named, not imported: `tools` must not import `connectors` (ADR-010).
    classifier = home_ports.direct_classifier()
    assert (classifier.__module__, classifier.__name__) == ("connectors.home_direct", "classify")
    assert home_ports.direct_executor() is None


def test_an_unregistered_classifier_is_never_direct(restore_ports):
    home_ports.unregister_direct_classifier()
    verdict = home_ports.classify_home_direct(**KWARGS)
    assert verdict == DirectVerdict(direct=False, reason=home_ports.DIRECT_NOT_AVAILABLE)


def test_a_raising_classifier_is_never_direct(restore_ports):
    def boom(**_kwargs):
        raise RuntimeError("broken")

    home_ports.register_direct_classifier(boom)
    assert home_ports.classify_home_direct(**KWARGS).direct is False


@pytest.mark.parametrize("answer", [True, {"direct": True}, "direct", None])
def test_a_classifier_answering_anything_but_a_verdict_is_never_direct(restore_ports, answer):
    home_ports.register_direct_classifier(lambda **_kwargs: answer)
    verdict = home_ports.classify_home_direct(**KWARGS)
    assert verdict.direct is False and verdict.reason == home_ports.DIRECT_NOT_AVAILABLE


def test_the_classifier_receives_every_keyword(restore_ports):
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return DirectVerdict(direct=True)

    home_ports.register_direct_classifier(spy)
    assert home_ports.classify_home_direct(**KWARGS, state=None).direct is True
    assert seen == {**KWARGS, "state": None}


def test_no_executor_reports_not_direct(restore_ports):
    home_ports.unregister_direct_executor()
    run = home_ports.run_home_direct(
        workspace=None, actor=None, message=None, origin="chat", channel="web"
    )
    assert run == DirectRun(outcome="not_direct", reason="not_available")


def test_a_raising_executor_is_an_error_never_a_card(restore_ports):
    def boom(**_kwargs):
        raise RuntimeError("after the send")

    home_ports.register_direct_executor(boom)
    run = home_ports.run_home_direct(
        workspace=None, actor=None, message=None, origin="node", channel="node_voice", node_id="n"
    )
    assert (run.outcome, run.reason) == ("error", "executor_failed")
    home_ports.register_direct_executor(lambda **_kwargs: "sent")
    run = home_ports.run_home_direct(
        workspace=None, actor=None, message=None, origin="chat", channel="web"
    )
    assert run.outcome == "error"


def test_verdict_and_run_refuse_inconsistent_values():
    with pytest.raises(ValueError):
        DirectVerdict(direct=True, reason="not_flagged")
    with pytest.raises(ValueError):
        DirectVerdict(direct=False)
    with pytest.raises(ValueError):
        DirectRun(outcome="approved")


def test_branch_1b_answers_not_direct_for_every_origin():
    """With no executor, a would-be direct call is never sent (the send
    path is the executor's; `connectors/tests/test_home_direct_flag.py`
    proves no HA request through the real chat view)."""
    for origin, channel in (("voice", "web"), ("chat", "web"), ("node", "node_voice")):
        run = home_ports.run_home_direct(
            workspace=None, actor=None, message=None, origin=origin, channel=channel
        )
        assert run.outcome == home_ports.DIRECT_RUN_NOT_DIRECT
