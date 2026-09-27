from celery import shared_task
from django.core.management import call_command


@shared_task
def flush_expired_tokens():
    """Daily beat job (config/celery.py): drop expired rows from
    simplejwt's OutstandingToken/BlacklistedToken tables -- with rotation +
    blacklisting (Phase 4 branch 1) every refresh adds rows. An expired
    token is rejected on its `exp` claim alone, so removing its blacklist
    row never re-enables it."""
    call_command("flushexpiredtokens")
