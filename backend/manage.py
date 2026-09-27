#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def main():
    # Deliberately still the dev default (Phase 4 branch 1), unlike the
    # WSGI/ASGI/Celery entry points (prod): every documented host-side
    # command (`uv run python manage.py ...` from backend/, README, runbooks,
    # CI) relies on it, and manage.py never serves traffic in production --
    # the daily stack's migrate step sets DJANGO_SETTINGS_MODULE explicitly
    # (deploy/docker/compose.daily.yml).
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Did you run `uv sync` and activate the environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
