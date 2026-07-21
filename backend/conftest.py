import pytest


@pytest.fixture(autouse=True)
def _celery_eager_for_tests(settings):
    """MVP 2 §4: the whole backend test suite runs Celery in eager mode, so a
    `transaction.on_commit` dispatch (extraction job publish) executes the
    task inline instead of requiring a live broker. `EAGER_PROPAGATES` makes
    an eager task's own exceptions surface in the calling test rather than
    being swallowed, per the brief's negative-test requirement.

    Dev/Compose leave both flags off by default (`config/settings/base.py`)
    so the real worker container keeps processing tasks out-of-process; only
    this test fixture (and the e2e CI job's env) turns eager mode on.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
