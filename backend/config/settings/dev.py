import os

# Set BEFORE base is imported: base.py only allows its hard-coded
# DJANGO_SECRET_KEY fallback when DEBUG is on, and reads DEBUG from the
# environment. An explicit, non-empty DJANGO_DEBUG in the environment still
# wins (an empty one -- e.g. from a copied .env.example -- counts as unset).
if not os.environ.get("DJANGO_DEBUG"):
    os.environ["DJANGO_DEBUG"] = "true"

from .base import *  # noqa: E402, F403
from .base import env  # noqa: E402

DEBUG = True

# Single-owner system (ADR-008): signup is off in dev too — the owner
# account comes from `manage.py bootstrap_owner`. The Playwright e2e smoke
# is the one flow still exercising signup (until Phase 4's device-trust
# identity); it must opt in explicitly with SIGNUP_ENABLED=true.
SIGNUP_ENABLED = env.bool("SIGNUP_ENABLED", default=False)
