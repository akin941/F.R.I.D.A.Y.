from core.exceptions import FridayError


class GatewayError(FridayError):
    """Raised when a gateway call ultimately fails. `error_type` is a short,
    secret-safe string (provider_error, timeout, rate_limit, auth_error,
    client_disconnect, no_provider, unknown).

    `retryable` classifies whether the *caller* (e.g. a background job) may
    reasonably retry the whole call: timeouts, 5xx, rate limits and
    `no_provider` are retryable; auth/permanent-4xx errors and unclassified
    failures are not. It reflects the underlying `AdapterError.retryable`
    when one is available and defaults to `False` otherwise — a safe default,
    since an unknown failure should not be assumed transient.
    """

    def __init__(self, error_type, message="", *, retryable=False):
        self.error_type = error_type
        self.retryable = retryable
        super().__init__(message or error_type)


class BudgetExceeded(GatewayError):
    """A configured `AIBudget` cap refused the call before any provider was
    selected (Phase 6 A3). Not retryable: the window must roll over or the
    owner must raise the cap. `budget`/`window` name which cap tripped
    (`"daily"`, `"monthly"` or `"session"`) -- no amounts, no content."""

    def __init__(self, budget="", window=""):
        self.budget = budget
        self.window = window
        super().__init__(
            "budget_exceeded", f"AI budget {budget!r} ({window}) exhausted", retryable=False
        )


class NoProviderAvailable(GatewayError):
    def __init__(self, message="No active LLM provider is available"):
        # No provider configured/active is itself a transient operational
        # state (a provider may become active moments later) — retryable.
        super().__init__("no_provider", message, retryable=True)
