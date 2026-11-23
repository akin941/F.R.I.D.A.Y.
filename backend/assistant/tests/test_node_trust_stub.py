"""Phase 10 brief D9: the branch-3 trust-check stub. Branch 4 replaced its
registration in `AssistantConfig.ready()` with the real check
(`assistant/tests/test_trust_check.py` pins that); the stub stays in
`tools.node_action_ports` only so a test can register it, and the closeout
`trust_check` preflight fails while it is the registered function."""

from tools import node_action_ports


def test_the_stub_is_marked_and_only_warns():
    stub = node_action_ports.pending_trust_check
    assert stub.is_stub is True
    verdict = stub(workspace=None, actor=None, capability="x", args={})
    assert (verdict.verdict, verdict.reason_code) == ("warn", "trust_check_pending")


def test_ready_no_longer_registers_the_stub():
    import assistant
    from assistant.apps import AssistantConfig

    state = node_action_ports.snapshot_ports()
    node_action_ports.reset_ports()
    try:
        AssistantConfig("assistant", assistant).ready()
        registered = node_action_ports.registered_trust_check()
        assert registered is not node_action_ports.pending_trust_check
        assert getattr(registered, "is_stub", False) is False
    finally:
        node_action_ports.restore_ports(state)


def test_an_unregistered_port_blocks():
    state = node_action_ports.snapshot_ports()
    node_action_ports.reset_ports()
    try:
        assert node_action_ports.trust_check() is None
        assert node_action_ports.UNCONFIGURED.verdict == "block"
        assert node_action_ports.UNCONFIGURED.reason_code == "trust_check_unavailable_config"
    finally:
        node_action_ports.restore_ports(state)
