"""Harness v3 drivers (Phase 10 brief D10, branch 6).

A driver replaces only the "run the CLI once" step of an implementation run
with a long-lived, bidirectional session over the container's stdio. Gate A,
the wrapper-owned test run, the cross-vendor review and Gate B are
unchanged; coder != reviewer still holds. Discovery and review keep the CLI
path in this phase.

Selection: `HARNESS_DRIVER_CODEX` in `DRIVER_CHOICES["codex_cli"]`, honoured
only while `HARNESS_DRIVERS_ENABLED` is true (otherwise the CLI path runs,
whatever the per-agent value says) and only once the owner's third-party
terms check (d4) is recorded in `HARNESS_DRIVER_TERMS_CHECKED_ON`. The
system checks in `harness.checks` refuse to boot on a non-`cli` driver while
disabled or without d4; `selected_driver` re-checks at run time (the Celery
worker runs no system checks) and fails closed.

Import contract (pyproject, D14): `harness.drivers` imports only `harness`,
`core`, `approvals`, `ai_core` and `audit`.
"""

import datetime

from django.conf import settings

DRIVER_CLI = "cli"
DRIVER_CODEX_APP_SERVER = "app_server"

# Allowed values per agent. Branch 7 adds `"claude_code": ("cli", "agent_sdk")`.
DRIVER_CHOICES = {
    "codex_cli": (DRIVER_CLI, DRIVER_CODEX_APP_SERVER),
}
# The setting that selects each agent's driver.
DRIVER_SETTINGS = {
    "codex_cli": "HARNESS_DRIVER_CODEX",
}


class DriverConfigurationError(Exception):
    """A driver is selected but may not run (disabled, d4 missing, unknown
    value). The implementation job fails closed (`runner_error`)."""


def configured_driver(agent):
    """The raw per-agent setting value (`cli` for an agent without one)."""
    setting = DRIVER_SETTINGS.get(str(agent))
    if setting is None:
        return DRIVER_CLI
    return str(getattr(settings, setting, DRIVER_CLI) or DRIVER_CLI)


def terms_checked_on():
    """The d4 date as a `date`, or None when empty or not an ISO date."""
    raw = str(getattr(settings, "HARNESS_DRIVER_TERMS_CHECKED_ON", "") or "").strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def drivers_enabled():
    return getattr(settings, "HARNESS_DRIVERS_ENABLED", False) is True


def selected_driver(agent):
    """The driver an implementation run of `agent` uses now. `cli` while
    drivers are disabled (regression path). Raises
    `DriverConfigurationError` for an enabled non-`cli` value that is
    unknown for this agent or lacks the d4 record."""
    if not drivers_enabled():
        return DRIVER_CLI
    value = configured_driver(agent)
    if value == DRIVER_CLI:
        return DRIVER_CLI
    if value not in DRIVER_CHOICES.get(str(agent), (DRIVER_CLI,)):
        raise DriverConfigurationError(f"unknown driver {value!r} for agent {agent!r}")
    if terms_checked_on() is None:
        raise DriverConfigurationError(
            "HARNESS_DRIVER_TERMS_CHECKED_ON is empty: the third-party terms check (d4) "
            "must be recorded before any driver is enabled"
        )
    return value


def driver_status():
    """Per-agent driver state for the closeout preflight (`harness_drivers`)
    and the system checks: the configured value, whether it would run, the
    d4 date, the pin and whether its snapshot is provisional."""
    from harness.drivers.schema import codex_pin_errors, load_codex_snapshot

    rows = []
    for agent, choices in DRIVER_CHOICES.items():
        value = configured_driver(agent)
        snapshot = None
        pin_errors = []
        if agent == "codex_cli":
            pin_errors = codex_pin_errors()
            try:
                snapshot = load_codex_snapshot(settings.HARNESS_CODEX_APP_SERVER_VERSION)
            except (OSError, ValueError):
                snapshot = None
        rows.append(
            {
                "agent": agent,
                "driver": value,
                "known": value in choices,
                "enabled": drivers_enabled() and value != DRIVER_CLI,
                "terms_checked_on": terms_checked_on(),
                "pin": getattr(settings, "HARNESS_CODEX_APP_SERVER_VERSION", ""),
                "provisional": snapshot is None or snapshot.provisional,
                "pin_errors": pin_errors,
            }
        )
    return rows
