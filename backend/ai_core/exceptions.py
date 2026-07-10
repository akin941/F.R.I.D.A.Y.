from core.exceptions import JarvisError


class GatewayError(JarvisError):
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


class NoProviderAvailable(GatewayError):
    def __init__(self, message="No active LLM provider is available"):
        # No provider configured/active is itself a transient operational
        # state (a provider may become active moments later) — retryable.
        super().__init__("no_provider", message, retryable=True)
