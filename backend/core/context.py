"""Request-scoped context shared across layers without passing arguments around.

Currently holds only the request id; audit (and later ModelUsage/structlog) read
it to correlate records with the originating HTTP request.
"""

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id():
    return request_id_var.get()
