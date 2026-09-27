"""The optional `health_url` probe (brief D10): one GET, no redirects
followed, no retries, no proxy or credential from the environment, a short
timeout. Only the status class is used (2xx = pass); the body is never read
into anything. Tests replace `_http_get`."""

import logging

import urllib3
from django.conf import settings

logger = logging.getLogger(__name__)


def _http_get(url, *, timeout):
    """Returns the HTTP status code; raises on a transport error."""
    with urllib3.PoolManager(retries=False, timeout=timeout) as pool:
        response = pool.request("GET", url, redirect=False, preload_content=False)
        try:
            return response.status
        finally:
            response.release_conn()


def check_url(url):
    """True for a 2xx answer, False for anything else (3xx included -- a
    redirect is not followed and is not healthy)."""
    timeout = urllib3.Timeout(total=settings.DEPLOY_HEALTH_URL_TIMEOUT_SECONDS)
    try:
        status_code = _http_get(url, timeout=timeout)
    except Exception:
        logger.info("health_url probe failed", exc_info=True)
        return False
    return 200 <= status_code < 300
