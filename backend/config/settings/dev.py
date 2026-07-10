from .base import *  # noqa: F403
from .base import env

DEBUG = True

# Signup is open by default in development only.
SIGNUP_ENABLED = env.bool("SIGNUP_ENABLED", default=True)
