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

# Development has no trusted reverse proxy in front of `next dev`, so the
# BFF cannot forward a trustworthy client IP (Phase 4 branch 2 step-0
# spike): PIN pairing stays usable locally with LAN gating off. Production
# defaults to false (config/settings/base.py).
DEVICE_PAIRING_ALLOW_UNKNOWN_IP = env.bool("DEVICE_PAIRING_ALLOW_UNKNOWN_IP", default=True)
# Django admin (break-glass, Phase 4 Q3) is routed in development only by
# default; production opts in explicitly.
DJANGO_ADMIN_ENABLED = env.bool("DJANGO_ADMIN_ENABLED", default=True)
