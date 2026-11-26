"""`deploy/home/friday-node-linux.service` (D10): a hardened systemd user
unit whose only writable paths are the node's state and runtime
directories, and whose binary is where `make linux-node-install` puts it."""

import configparser
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
UNIT = REPO / "deploy" / "home" / "friday-node-linux.service"


def unit():
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(UNIT.read_text(encoding="utf-8"))
    return parser


def test_the_unit_runs_the_installed_daemon_hardened():
    service = unit()["Service"]
    assert (
        service["ExecStart"] == "%h/.local/share/friday-node-linux/.venv/bin/friday-node-linux run"
    )
    assert service["ExecReload"] == "/bin/kill -HUP $MAINPID"
    assert service["Restart"] == "on-failure"
    assert service["NoNewPrivileges"] == "yes"
    assert service["ProtectSystem"] == "strict"
    assert service["UMask"] == "0077"
    assert service["ReadWritePaths"].split() == ["%S/friday-node", "%t/friday-node"]
    assert service["StateDirectory"] == "friday-node"
    assert service["RuntimeDirectory"] == "friday-node"
    assert "User" not in service  # a user unit: it runs as the desktop user
    assert unit()["Install"]["WantedBy"] == "default.target"


def test_the_makefile_installs_where_the_unit_looks():
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "LINUX_NODE_HOME ?= $(HOME)/.local/share/friday-node-linux" in makefile
    assert "install -m 0644 deploy/home/friday-node-linux.service" in makefile
