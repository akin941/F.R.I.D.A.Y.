"""`FRIDAY_*` environment variables with a fallback to their deprecated
pre-rename `JARVIS_*` spelling (the assistant was renamed from Jarvis to
Friday on 2026-11-20; docs/runbooks/rename-jarvis-to-friday.md).

Stdlib only -- no Django import -- so settings, management commands and the
standalone CLI can all use it. Remove the fallback once no install sets the
legacy names any more.
"""

import os

PREFIX = "FRIDAY_"
LEGACY_PREFIX = "JARVIS_"


def legacy_name(name: str) -> str:
    if not name.startswith(PREFIX):
        raise ValueError(f"{name} is not a {PREFIX}* variable.")
    return LEGACY_PREFIX + name[len(PREFIX) :]


def getenv(name: str, default: str = "", *, environ=None) -> str:
    """The value of `FRIDAY_X`, else the deprecated `JARVIS_X`, else
    `default`. An empty value counts as unset (Compose passes `${X:-}`)."""
    environ = os.environ if environ is None else environ
    return environ.get(name) or environ.get(legacy_name(name)) or default
