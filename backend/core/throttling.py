"""Shared DRF throttle classes (Phase 4 branch 1)."""

import logging

from rest_framework.exceptions import APIException
from rest_framework.throttling import ScopedRateThrottle as DRFScopedRateThrottle

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
