"""Phase 10 brief D9, branch 3: `AssistantConfig.ready()` registers the
explicit trust-check stub; removing the registration leaves the port
unregistered, which blocks every proposal."""

from pathlib import Path

from tools import node_action_ports


def test_ready_registers_the_explicit_stub():
    import assistant
    from assistant.apps import AssistantConfig

    node_action_ports.reset_ports()
    try:
        AssistantConfig("assistant", assistant).ready()
        registered = node_action_ports.registered_trust_check()
        assert registered is node_action_ports.pending_trust_check
        assert registered.is_stub is True
        verdict = registered(workspace=None, actor=None, capability="x", args={})
        assert (verdict.verdict, verdict.reason_code) == ("warn", "trust_check_pending")
    finally:
        node_action_ports.register_trust_check(node_action_ports.pending_trust_check)


def test_the_registration_is_one_explicit_line():
    source = (Path(__file__).resolve().parents[1] / "apps.py").read_text("utf-8")
    assert source.count("register_trust_check(pending_trust_check)") == 1


def test_an_unregistered_port_blocks():
    node_action_ports.reset_ports()
    try:
        assert node_action_ports.trust_check() is None
        assert node_action_ports.UNCONFIGURED.verdict == "block"
        assert node_action_ports.UNCONFIGURED.reason_code == "trust_check_unavailable_config"
    finally:
        node_action_ports.register_trust_check(node_action_ports.pending_trust_check)
