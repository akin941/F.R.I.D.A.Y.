from .base import *  # noqa: F403
from .base import env

DEBUG = True

# Single-owner system (ADR-008): signup is off in dev too — the owner
# account comes from `manage.py bootstrap_owner`. The Playwright e2e smoke
# is the one flow still exercising signup (until Phase 4's device-trust
# identity); it must opt in explicitly with SIGNUP_ENABLED=true.
SIGNUP_ENABLED = env.bool("SIGNUP_ENABLED", default=False)
