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

# The eval spend cap's purposes (D9, D11). `chat` is here for the
# compression compare (`memory_eval compare --compression chat`): both arms
# answer under `chat` and must be eval-only spend under the same cap. In
# this database every `chat` row is an eval row (each carries `eval_run_id`
# and `eval_arm` in its context report), never the owner's chat traffic.
# The compare refuses a purpose that is not listed here, and one the
# `learning_eval` AIBudget row (seeded from this tuple) does not cover.
AI_LEARNING_EVAL_PURPOSES = ("memory.eval_answer", "chat")
# Compression stays off under the eval settings; the compare switches it on
# for arm B only, for the candidate purpose only.
AI_COMPRESSION_ENABLED = False
AI_COMPRESSION_PURPOSES = frozenset()

# No broker is needed: the runner embeds setup memories in-process.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
# A CLI process: no shared cache is needed (the `test` settings precedent).
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
