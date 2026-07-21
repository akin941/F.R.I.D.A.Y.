import uuid

from core.context import request_id_var


class RequestIDMiddleware:
    """Attach a unique request id to every request/response.

    The request id is the correlation key across audit logs, structured logs and
    (from the gateway branch on) ModelUsage records. It is also published to
    `core.context.request_id_var` so services can read it without plumbing the
    request object through every call — and reset afterwards so ids never leak
    between requests sharing a worker.
    """

    HEADER = "X-Request-ID"
    # audit.AuditLog.request_id and ai_core.ModelUsage.request_id are both
    # CharField(max_length=64); an oversized client-supplied header would
    # otherwise reach those inserts and raise a DB-level DataError.
    MAX_LENGTH = 64

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.headers.get(self.HEADER)
        if supplied and len(supplied) <= self.MAX_LENGTH:
            request.request_id = supplied
        else:
            request.request_id = uuid.uuid4().hex
        token = request_id_var.set(request.request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response[self.HEADER] = request.request_id
        return response
