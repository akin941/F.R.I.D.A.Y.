"""The closed set of stable error codes the API emits (Phase 4 branch 3).

Every API error body carries a `code` from this set next to the legacy
English `detail`. Clients key their own (Turkish) messages off `code` and
must never show `detail` except as a technical detail.

This module is the single source of truth for the vocabulary:

- `core.tests.test_error_codes` enumerates every code the backend can emit
  (the generic ones below plus each domain exception class's `code`) and
  asserts it is listed here;
- the frontend's catalog test reads THIS FILE and asserts every code has a
  Turkish entry in `frontend/src/lib/i18n/tr.ts`.

Keep it a flat literal: the frontend test parses the quoted strings.
Adding a code = add it here + a Turkish message in the frontend catalog.
"""

# Generic codes: DRF's own `default_code` values (validation, auth,
# throttling, routing), plus the status-derived fallbacks
# `core.api_errors` assigns to hand-built error responses.
GENERIC_ERROR_CODES = frozenset(
    {
        "invalid",
        "parse_error",
        "authentication_failed",
        "not_authenticated",
        "permission_denied",
        # `core.permissions.IsWebChannel` / `IsOwner` (Phase 4 branch 2).
        "channel_forbidden",
        "owner_required",
        # A valid PAT of the wrong `kind` for the endpoint (Phase 4 branch 6:
        # an `openai` token on a CLI surface, or a `cli` token on the facade).
        "token_kind_forbidden",
        "not_found",
        "method_not_allowed",
        "not_acceptable",
        "unsupported_media_type",
        "throttled",
        "throttle_backend_unavailable",
        # simplejwt puts this in the body itself for a bad/expired JWT.
        "token_not_valid",
        "conflict",
        "error",
        "service_unavailable",
    }
)

# Domain codes, emitted by the per-app `_service_error_response` helpers
# from the domain exception's `code` attribute.
DOMAIN_ERROR_CODES = frozenset(
    {
        "approval.permission_denied",
        "approval.agent_forbidden",
        "approval.channel_forbidden",
        "approval.invalid_transition",
        "approval.expired",
        "connector.permission_denied",
        "connector.invalid_transition",
        "agent.permission_denied",
        "agent.invalid_transition",
        "harness.permission_denied",
        "harness.invalid_transition",
        "knowledge.permission_denied",
        "knowledge.duplicate",
        "knowledge.invalid_upload",
        "memory.permission_denied",
        "memory.invalid_transition",
        "memory.duplicate",
        "task.permission_denied",
        "task.invalid_transition",
        "task.draft_already_confirmed",
        "workspace.permission_denied",
    }
)

ERROR_CODES = GENERIC_ERROR_CODES | DOMAIN_ERROR_CODES
