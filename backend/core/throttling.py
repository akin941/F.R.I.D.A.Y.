"""Shared DRF throttle classes (Phase 4 branch 1)."""

import logging

from django.conf import settings
from rest_framework.exceptions import APIException
from rest_framework.throttling import ScopedRateThrottle as DRFScopedRateThrottle
from rest_framework.throttling import SimpleRateThrottle

from core.client_ip import trusted_client_ip

logger = logging.getLogger(__name__)


class ThrottleBackendUnavailable(APIException):
    status_code = 503
    default_detail = "Rate-limit backend unavailable; try again shortly."
    default_code = "throttle_backend_unavailable"


class ScopedRateThrottle(DRFScopedRateThrottle):
    """`ScopedRateThrottle` that fails CLOSED with a deliberate 503 when its
    counter store is unreachable (Phase 4 branch 1 review P2). Counters live
    in the Redis cache; a Redis outage used to surface as an unhandled
    exception (500) on login/refresh/logout and every cost-bearing create.
    Failing open would silently drop the auth and cost limits, so the
    request is refused instead -- with a status clients can retry on."""

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception as exc:  # redis-py / cache backend connection errors
            logger.exception("Throttle backend unavailable for scope %s", self.scope)
            raise ThrottleBackendUnavailable() from exc


class CreateScopedRateThrottle(ScopedRateThrottle):
    """`ScopedRateThrottle` that counts only POST (create) requests, for
    list/create views where only the create drives cost -- listing stays
    free. Rates come from `DEFAULT_THROTTLE_RATES[view.throttle_scope]`."""

    def allow_request(self, request, view):
        if request.method != "POST":
            return True
        return super().allow_request(request, view)


_PERIOD_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_rate(rate):
    """`"<count>/<n><unit>"` -> `(count, seconds)`, where the multiplier is
    optional ("5/10m", "60/h", "3/30s"). DRF's own parser only accepts a bare
    unit (and silently treats "10min" as garbage)."""
    if rate is None:
        return None, None
    count, _, period = rate.partition("/")
    period = period.strip()
    digits = ""
    while period and period[0].isdigit():
        digits, period = digits + period[0], period[1:]
    if not period or period[0] not in _PERIOD_SECONDS:
        raise ValueError(f"Invalid throttle rate {rate!r}.")
    return int(count), int(digits or 1) * _PERIOD_SECONDS[period[0]]


class _SettingsRateThrottle(SimpleRateThrottle):
    """A fail-closed SimpleRateThrottle whose rate is read from a Django
    setting at request time (so it can use `parse_rate`'s multi-unit
    periods and be overridden per test)."""

    rate_setting = ""

    def get_rate(self):
        return getattr(settings, self.rate_setting)

    def parse_rate(self, rate):
        return parse_rate(rate)

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception as exc:
            logger.exception("Throttle backend unavailable for scope %s", self.scope)
            raise ThrottleBackendUnavailable() from exc


class EnrollPerSourceThrottle(_SettingsRateThrottle):
    """Tier 1 of the enrollment throttle (Phase 4 branch 2, plan-review
    round 1 HIGH): a tight limit per BFF-key-validated client IP
    (`core.client_ip`). No validated IP -> this tier does not apply (only
    the global ceiling does -- the accepted, documented residual risk)."""

    scope = "enroll_source"
    rate_setting = "ENROLL_RATE_PER_SOURCE"

    def get_cache_key(self, request, view):
        client_ip = trusted_client_ip(request)
        if client_ip is None:
            return None
        return self.cache_format % {"scope": self.scope, "ident": client_ip}


class EnrollGlobalThrottle(_SettingsRateThrottle):
    """Tier 2: a much looser install-wide ceiling, so one source hitting its
    own limit never exhausts the bucket the owner needs."""

    scope = "enroll_global"
    rate_setting = "ENROLL_RATE_GLOBAL"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": "all"}


class FacadeTokenThrottle(_SettingsRateThrottle):
    """Per-token rate limit of the OpenAI-compatible facade (Phase 4
    branch 6): FACADE_RATE per PersonalAccessToken, whatever the client's
    IP. Keyed on the ChannelPrincipal's `token_id`; a request without one
    never reaches a facade view (bearer-only), so it is not counted here."""

    scope = "openai"
    rate_setting = "FACADE_RATE"

    def get_cache_key(self, request, view):
        token_id = getattr(getattr(request, "auth", None), "token_id", None)
        if token_id is None:
            return None
        return self.cache_format % {"scope": self.scope, "ident": token_id}
