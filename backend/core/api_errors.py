"""A stable `code` on every API error body (Phase 4 branch 3).

Two cooperating hooks, both wired in `REST_FRAMEWORK`:

- `exception_handler` (EXCEPTION_HANDLER): for a raised DRF exception it
  records the exception's own code -- `exc.default_code` for a plain
  `APIException` (`not_authenticated`, `permission_denied`, `throttled`,
  ...), `invalid` for a `ValidationError` (whose `get_codes()` is a
  per-field structure, not one code).
- `CodedJSONRenderer` (first of DEFAULT_RENDERER_CLASSES): the backstop
  for error responses a view builds by hand (`Response({"detail": ...},
  status=400)`, a bodiless `Response(status=404)`): any >= 400 body
  without a string `code` gets the status-derived fallback below.

`detail` is kept untouched for compatibility; clients show their own
message for `code` and `detail` only as technical detail. Every code must
be listed in `core.error_codes`.

A body that already has a `code` key is left alone: simplejwt's own
`{"detail", "code": "token_not_valid", "messages"}` body, or a validation
error for a serializer field literally named `code` (the client then
treats the non-string value as absent and falls back to the status).
"""

from rest_framework.exceptions import APIException, ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from core.error_codes import ERROR_CODES

STATUS_FALLBACK_CODES = {
    400: "invalid",
    401: "not_authenticated",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    415: "unsupported_media_type",
    429: "throttled",
    503: "service_unavailable",
}


def fallback_code(status_code: int) -> str:
    return STATUS_FALLBACK_CODES.get(status_code, "error")


def _with_code(data, code):
    """Return `data` carrying `code`, without clobbering an existing key."""
    if data is None or data == "":
        return {"code": code}
    if isinstance(data, dict):
        if "code" in data:
            return data
        return {**data, "code": code}
    if isinstance(data, list):
        # A bare list body (`raise ValidationError(["..."])`) has nowhere to
        # hang a key; wrap it so the body is still an object with a code.
        return {"errors": data, "code": code}
    return data


def exception_handler(exc, context):
    # Imported here, not at module level: `rest_framework.views` resolves
    # DEFAULT_RENDERER_CLASSES (-> this module's renderer) at import time.
    from rest_framework.views import exception_handler as drf_exception_handler

    response = drf_exception_handler(exc, context)
    if response is None or not isinstance(exc, APIException):
        return response
    if isinstance(exc, ValidationError):
        code = "invalid"
    else:
        codes = exc.get_codes()
        # Only registered codes leave the building: a third-party exception
        # raised with an ad-hoc `code=` (simplejwt's "user_inactive", ...)
        # degrades to its class's default code, then to the status fallback,
        # instead of widening the vocabulary.
        candidates = [codes if isinstance(codes, str) else None, exc.default_code]
        code = next(
            (c for c in candidates if c in ERROR_CODES), fallback_code(response.status_code)
        )
    response.data = _with_code(response.data, code)
    return response


class CodedJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = (renderer_context or {}).get("response")
        if response is not None and response.status_code >= 400:
            data = _with_code(data, fallback_code(response.status_code))
        return super().render(data, accepted_media_type, renderer_context)


def service_error_response(exc, status_code):
    """The body every `_service_error_response` helper returns for a domain
    exception: its message as `detail` and its stable `code` (the
    exception class's `code` attribute, else the status fallback)."""
    code = getattr(exc, "code", None) or fallback_code(status_code)
    return Response({"detail": str(exc), "code": code}, status=status_code)
