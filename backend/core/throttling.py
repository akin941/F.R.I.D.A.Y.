"""Shared DRF throttle classes (Phase 4 branch 1)."""

from rest_framework.throttling import ScopedRateThrottle


class CreateScopedRateThrottle(ScopedRateThrottle):
    """`ScopedRateThrottle` that counts only POST (create) requests, for
    list/create views where only the create drives cost -- listing stays
    free. Rates come from `DEFAULT_THROTTLE_RATES[view.throttle_scope]`."""

    def allow_request(self, request, view):
        if request.method != "POST":
            return True
        return super().allow_request(request, view)
