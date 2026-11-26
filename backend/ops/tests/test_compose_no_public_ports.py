"""Phase 13 branch 7 (brief D14, Risks "LAN exposure creeps"): every port
any compose file in the repository publishes is bound to 127.0.0.1, no
service uses the host network, and `compose.home.yml` publishes nothing and
binds only the two status files, read-only. Docker-published ports bypass
`ufw`, so this test is the guard; the only LAN-facing listener is Caddy on
the host (`deploy/home/Caddyfile.example`). Parsed YAML, no Docker."""

import configparser
import os
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
HOME_COMPOSE = REPO / "deploy" / "docker" / "compose.home.yml"
HOME_DIR = REPO / "deploy" / "home"
_SKIP_DIRS = {".git", "node_modules", ".venv", ".claude", ".next", "__pycache__"}
_COMPOSE_NAME = re.compile(r"^(docker-)?compose(\.[\w-]+)*\.ya?ml$")
LOOPBACK = "127.0.0.1"

if not (REPO / "deploy" / "docker").is_dir():  # a backend-only image, not a checkout
    pytest.skip("no repository checkout around the backend", allow_module_level=True)
HOME_SETTINGS = (
    "HOME_BACKUP_STATUS_PATH",
    "HOME_RESTORE_DRILL_STATUS_PATH",
    "HOME_BACKUP_WARN_HOURS",
    "HOME_BACKUP_FAIL_HOURS",
    "HOME_DRILL_WARN_DAYS",
    "HOME_DRILL_FAIL_DAYS",
    "HOME_FDE_CONFIRMED_ON",
)


def compose_files():
    found = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        found.extend(Path(root) / name for name in sorted(files) if _COMPOSE_NAME.match(name))
    return found


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def port_problems(document):
    """`service: entry` for every published port not bound to 127.0.0.1 and
    every host-network service in one parsed compose document."""
    problems = []
    for service, spec in (document.get("services") or {}).items():
        spec = spec or {}
        if spec.get("network_mode") == "host":
            problems.append(f"{service}: network_mode host")
        for entry in spec.get("ports") or []:
            if isinstance(entry, dict):
                published = entry.get("published")
                if published is not None and entry.get("host_ip") != LOOPBACK:
                    problems.append(f"{service}: {entry}")
                continue
            text = str(entry)
            # host_ip:published:target[/proto] with host_ip 127.0.0.1; a bare
            # "target" or "published:target" binds every interface.
            if not text.startswith(f"{LOOPBACK}:") or text.count(":") != 2:
                problems.append(f"{service}: {text}")
    return problems


def test_the_repository_has_the_expected_compose_files():
    names = {path.relative_to(REPO).as_posix() for path in compose_files()}
    assert {
        "deploy/docker/compose.yml",
        "deploy/docker/compose.daily.yml",
        "deploy/docker/compose.home.yml",
    } <= names


@pytest.mark.parametrize("path", compose_files(), ids=lambda p: p.relative_to(REPO).as_posix())
def test_every_published_port_is_loopback_only(path):
    assert port_problems(_load(path)) == []


@pytest.mark.parametrize(
    "ports",
    [
        ["0.0.0.0:8001:8000"],
        ["8001:8000"],
        ["8000"],
        [8000],
        ["192.168.1.20:443:443"],
        ["[::]:8001:8000"],
        [{"target": 8000, "published": 8001}],
        [{"target": 8000, "published": "8001", "host_ip": "0.0.0.0"}],
    ],
)
def test_the_guard_refuses_non_loopback_ports(ports):
    document = {"services": {"web": {"ports": ports}}}
    assert port_problems(document) != []


def test_the_guard_refuses_the_host_network():
    assert port_problems({"services": {"web": {"network_mode": "host"}}}) == [
        "web: network_mode host"
    ]


@pytest.mark.parametrize(
    "ports",
    [
        ["127.0.0.1:8001:8000"],
        ["127.0.0.1:5353:5353/udp"],
        [{"target": 1, "host_ip": LOOPBACK, "published": 2}],
    ],
)
def test_the_guard_accepts_loopback_ports(ports):
    assert port_problems({"services": {"web": {"ports": ports}}}) == []


def test_the_home_override_publishes_nothing():
    document = _load(HOME_COMPOSE)
    for service, spec in document["services"].items():
        assert "ports" not in (spec or {}), service
        assert (spec or {}).get("network_mode") is None, service


def test_the_home_override_binds_only_the_two_status_files_read_only():
    document = _load(HOME_COMPOSE)
    services = document["services"]
    assert set(services) == {"web", "worker"}
    volumes = services["web"]["volumes"]
    assert [v["target"] for v in volumes] == [
        "/run/friday-home/status.json",
        "/run/friday-home/drill.json",
    ]
    for volume in volumes:
        assert volume["type"] == "bind"
        assert volume["read_only"] is True
        # Never a directory created by Docker in place of a missing file.
        assert volume["bind"]["create_host_path"] is False
        # One file each, never the backup directory (dumps, the env file).
        assert volume["source"].endswith(("/status.json", "/drill.json"))
    assert "volumes" not in services["worker"]


def test_the_home_override_sets_every_home_setting_in_web_and_worker():
    services = _load(HOME_COMPOSE)["services"]
    web = services["web"]["environment"]
    worker = services["worker"]["environment"]
    for name in HOME_SETTINGS:
        assert name in web
        assert name in worker
    assert web["HOME_BACKUP_STATUS_PATH"] == "/run/friday-home/status.json"
    assert web["HOME_RESTORE_DRILL_STATUS_PATH"] == "/run/friday-home/drill.json"
    # Nothing is mounted in the worker, so its status paths stay unset.
    assert worker["HOME_BACKUP_STATUS_PATH"] == ""
    assert worker["HOME_RESTORE_DRILL_STATUS_PATH"] == ""


def test_every_home_setting_exists_in_django_settings_and_env_example(settings):
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    for name in HOME_SETTINGS:
        assert hasattr(settings, name), name
        assert f"# {name}=" in example, name
    assert settings.HOME_BACKUP_WARN_HOURS == 26
    assert settings.HOME_BACKUP_FAIL_HOURS == 72
    assert settings.HOME_DRILL_WARN_DAYS == 35
    assert settings.HOME_DRILL_FAIL_DAYS == 90


def _unit(name):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(HOME_DIR / name, encoding="utf-8")
    return parser


def test_the_backup_timer_is_nightly_and_persistent():
    timer = _unit("friday-backup.timer")
    assert timer["Timer"]["OnCalendar"] == "*-*-* 03:10:00"
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_the_backup_service_runs_the_script_as_the_friday_user():
    service = _unit("friday-backup.service")["Service"]
    assert service["Type"] == "oneshot"
    assert service["User"] == "friday"
    assert service["ExecStart"] == "/usr/local/bin/friday-backup"
    assert service["NoNewPrivileges"] == "true"
    assert service["ReadWritePaths"] == "/var/backups/friday"


def test_the_caddyfile_binds_the_lan_address_only():
    text = (HOME_DIR / "Caddyfile.example").read_text(encoding="utf-8")
    directives = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "bind 192.168.1.20" in directives
    assert "admin off" in directives
    assert "auto_https off" in directives
    assert "reverse_proxy 127.0.0.1:8002" in directives
    assert not any("0.0.0.0" in line for line in directives)
    assert not any(line.startswith("bind") and line != "bind 192.168.1.20" for line in directives)
