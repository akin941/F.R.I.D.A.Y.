"""Eval settings (Phase 8B branch 3, brief D9): `manage.py memory_eval
run|baseline|compare` run only under this module.

- Its own database (`MEMORY_EVAL_DATABASE_NAME`, default `friday_eval`), on
  the same server as `DATABASE_URL`: eval cases are loaded as ordinary rows
  of that database and never reach the owner's live workspaces. Importing
  this module fails if the name would equal the live database's.
- The eval database **persists** between runs (D9): a run clears only the
  previous case's setup rows, never `ModelUsage`, so the `learning_eval`
  monthly cap holds across runs.
- `MEMORY_EVAL_MODE = True` is the flag the subcommands check; the live
  settings keep it False, and `memory_eval record` (which writes the
  metrics-only `MemoryEvalRun` into the LIVE database) refuses it.

Usage (docs/runbooks/memory-eval.md):

    DJANGO_SETTINGS_MODULE=config.settings.eval uv run python manage.py migrate
    DJANGO_SETTINGS_MODULE=config.settings.eval uv run python manage.py seed_ai_defaults
    DJANGO_SETTINGS_MODULE=config.settings.eval uv run python manage.py \\
        memory_eval baseline --set private
"""

from django.core.exceptions import ImproperlyConfigured

from .dev import *  # noqa: F403
from .dev import DATABASES, env

MEMORY_EVAL_MODE = True

_LIVE_DATABASE_NAME = DATABASES["default"].get("NAME", "")
MEMORY_EVAL_DATABASE_NAME = env("MEMORY_EVAL_DATABASE_NAME", default="friday_eval")
if not MEMORY_EVAL_DATABASE_NAME or MEMORY_EVAL_DATABASE_NAME == _LIVE_DATABASE_NAME:
    raise ImproperlyConfigured(
        "MEMORY_EVAL_DATABASE_NAME must name a database other than the live one."
    )
DATABASES["default"]["NAME"] = MEMORY_EVAL_DATABASE_NAME

# The eval spend cap's purposes. Branch 6 (D11) adds the purpose under a
# compression compare here, so both arms run under the same eval-only cap.
AI_LEARNING_EVAL_PURPOSES = ("memory.eval_answer",)

# No broker is needed: the runner embeds setup memories in-process.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
# A CLI process: no shared cache is needed (the `test` settings precedent).
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
