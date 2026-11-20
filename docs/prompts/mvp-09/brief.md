# MVP 9 — Integrations: Telegram Bot + CLI Channel

> Status: **plan approved (2026-07-13)** — 8 rounds of Codex plan-review
> (round 1: 2 P1; round 2: 2 P1; round 3: 3 P2; round 4: 1 P1 + 4 P2;
> round 5: 1 P2; round 6: 3 P2; round 7: 1 P2; round 8: 0 findings,
> APPROVE). Scope chosen by the user from `docs/integrations/README.md`'s
> four candidate integrations (Gmail/Calendar/Drive, GitHub, Telegram bot +
> CLI channel, local folder sync) — Telegram + CLI selected as MVP 9; the
> other three remain unscoped, later `integrations` work. All 4 P1s
> (global-PAT scope leak → per-view allowlist; Telegram group-chat linking
> unenforced → `chat.type == "private"` gate; PAT id-lookup/body-field
> workspace bypass → generalized `scoped_for_actor()`; PAT auth missing an
> `is_active` check) were escalated to the user and fixed with explicit
> sign-off, including one real architecture correction along the way
> (`PersonalAccessToken` moved from a would-be `integrations` app into
> `accounts`, since `assistant`/`agents`/`approvals` needing to import the
> authenticator directly contradicted the brief's own import-linter rules)
> and one real scope reduction (Telegram-relayed task_draft/tool_request
> drafts ended up with **no** confirm path in either channel — not
> Telegram, and not a later web-UI action either, since the web app's
> draft-confirm mechanism turned out to only work for the live SSE stream
> that produced it, never a reopened past turn). All P2/P3 findings (PAT
> workspace-presence-check bypass, serializer-level workspace injection,
> Telegram link-status/unlink endpoints, channel threaded through the
> assistant-reply persistence path too, `LinkedTelegramChat`
> `(user, workspace)` uniqueness + replace-on-relink, import-linter
> self-contradiction, PAT ownership filtering, Telegram message chunking
> past the 4096-char limit, atomic link-code consumption, missing `pytest`
> `testpaths` entry) were closed autonomously per standing protocol.
>
> **Status: MVP 9 COMPLETE (2026-07-14)** — all 5 branches merged to main
> (`12ad4f9`, `6d69147`, `db00dee`, `65ea9fa`, `7780919`). See the Branch
> plan table below for per-branch Codex code-review round counts and
> findings.

## Goal and user value

Every surface Friday has shipped so far (chat, tasks, approvals, agents,
skills) is reached through exactly one channel: the web app, authenticated by
a short-lived JWT a human obtained by logging in with a password. That's the
right *only* channel while Friday has no other client, but it's also the
whole reason a personal AI OS is worth building: it should be reachable from
wherever the user actually is — a phone's Telegram app, a terminal — not only
a browser tab.

This MVP adds two new ways to *reach* Friday — a Telegram bot and an
authenticated CLI — without adding a single new way to *act*. Both are new
front doors onto the exact same chat pipeline (`assistant`) and, for the CLI,
the exact same `agents`/`approvals` endpoints the web app already calls nothing
new is exposed through them that isn't already reachable today. The one new
concept this MVP introduces on purpose is **channel trust**: Telegram and CLI
are new, are authenticated by a bearer secret instead of a password+JWT
login, and have no equivalent of "the browser tab you're already looking at
is the one you decide approvals from." `docs/integrations/README.md` already
names the constraint this demands: **no approvals from low-trust channels.**
Everything in this brief is built around making that one rule airtight, then
building the two channels on top of it.

## Relationship to existing systems — the boundary this MVP draws

- **Chat (`assistant`, MVP 2/6)**: unchanged. Telegram relays a user's
  message into the *exact same* `classify_intent` → `build_gateway_stream` →
  persist pipeline `ConversationMessageListView.post` already runs
  (`assistant/views.py:101-177`) — same router, same task-draft/tool-request
  drafting, same `ModelUsage` accounting. The CLI doesn't even relay: it's a
  raw HTTP client of the same endpoint, authenticated differently.
- **Agents (MVP 7) and Approvals (MVP 5)**: unchanged in behavior. A
  CLI-authenticated request can create an `Agent` from a goal or request a
  tool call exactly as a web request can (writer-role gated, same as today).
  The one new restriction is at the **decision** boundary: `approve`/`reject`
  on an `ApprovalRequest` (`approvals/services.py`) now additionally requires
  the deciding request to have arrived over the **web** channel — a
  Telegram- or CLI-authenticated actor can propose an action and can still
  legitimately hold a curator role in the workspace, but cannot be the one
  who decides it. This is the same "propose, never decide" shape MVP 8
  already established for skills (a skill can propose steps, never approve
  or execute them) — extended here from *what* can decide to *through which
  channel* a decision may be made.
- **Workspace scoping (ADR-001)**: every request, from every channel, still
  resolves to exactly one `(user, workspace)` pair and passes through
  `for_actor()` — nothing about this MVP creates a side door around that. The
  mechanism differs only in *how* the workspace is determined (see §1).

## Scope

### 1. `integrations` app + channel-trust foundation

New Django kernel app `integrations`, sibling to `agents`/`skills`
(ADR-002). Owns everything genuinely new to this MVP; nothing in `assistant`,
`approvals`, or `agents`'s own business logic changes — only how they resolve
**which workspace** a request acts in, and one new check inside `approvals`.

**The channel-trust primitive lives in `core`, not `integrations`** — this is
the one cross-cutting design decision the rest of the MVP hangs off, so it's
worth stating why up front: `approvals` must be able to ask "did this request
arrive over a low-trust channel?" without importing `integrations` (which
would create a needless `approvals <-> integrations` coupling neither app
otherwise needs — `integrations` never touches `ApprovalRequest`). Putting
the primitive in `core` — which every kernel app may already depend on, per
the existing "core must stay independent of kernel apps" contract meaning the
dependency is one-way *from* kernel apps *into* `core` — keeps `integrations`
and `approvals` mutually unaware of each other, each depending only on
`core`.

`core/channels.py`:
```python
CHANNEL_WEB = "web"        # default: standard password-login JWT, trusted
CHANNEL_CLI = "cli"        # low-trust: bearer PAT
CHANNEL_TELEGRAM = "telegram"  # low-trust: relayed, no direct request at all

@dataclass(frozen=True)
class ChannelPrincipal:
    """Set as `request.auth` by a low-trust authenticator (see §2).
    Absence of this (e.g. a plain JWTAuthentication auth object) means the
    request is CHANNEL_WEB -- default-trusted, not default-denied, since
    every pre-MVP-9 authenticator keeps working unmodified."""
    channel: str                 # CHANNEL_CLI today; CHANNEL_TELEGRAM never
                                  # appears here -- Telegram has no direct
                                  # request/response cycle with our API at
                                  # all (see §4), so there is no request to
                                  # authenticate as Telegram in the first
                                  # place.
    bound_workspace_id: str       # the one workspace this credential acts in

def request_channel(request) -> str:
    auth = getattr(request, "auth", None)
    return auth.channel if isinstance(auth, ChannelPrincipal) else CHANNEL_WEB

def is_low_trust(request) -> bool:
    return request_channel(request) != CHANNEL_WEB

def scoped_for_actor(request, queryset, workspace=None):
    """The one function every workspace-scoped view in the PAT allowlist
    (§2) must call instead of `<Model>.objects.for_actor(request.user,
    workspace=...)` directly. A Codex plan-review round-2 P1 finding caught
    that the earlier, narrower `effective_workspace_id()` design (below,
    superseded) only intercepted the query-param-reading call sites in
    list/create views -- it did nothing for a create endpoint that reads
    `workspace` from the request body, or for a detail/cancel/transition
    endpoint that looks up a row purely by id with no workspace parameter
    at all (e.g. `AgentDetailView`, `AgentCancelView`,
    `ApprovalRequestTransitionView`). Those paths would still resolve via
    plain `for_actor(request.user, workspace=None)`, which returns every
    workspace the user belongs to -- so a PAT bound to workspace A could
    still reach an object in workspace B by id, exactly the containment
    break this MVP exists to prevent. Fix: wrap the call itself, not the
    parameter parsing, so every for_actor() site in the allowlisted views
    -- list, create, detail, cancel, transition, no exceptions -- passes
    through here uniformly:

        scoped_for_actor(request, Agent.objects, workspace=requested)

    is a drop-in replacement for:

        Workspace-membership-filtered via Agent.objects.for_actor(request.user, workspace=requested)

    For a CHANNEL_WEB request, behavior is unchanged (whatever `workspace`
    the view already computed -- from a query param, a body field, or
    None for a bare by-id lookup -- passes through as-is). For a
    ChannelPrincipal-authenticated request, `workspace` is always forced to
    `bound_workspace_id`, regardless of what the client supplied or omitted
    -- a by-id lookup for an object in a different workspace then returns
    nothing (404, consistent with the existing "never leak existence"
    convention), and a create with a mismatched or absent body `workspace`
    field is silently corrected to the bound one rather than rejected,
    exactly like the original query-param case."""
    auth = getattr(request, "auth", None)
    if isinstance(auth, ChannelPrincipal):
        workspace = auth.bound_workspace_id
    return queryset.for_actor(request.user, workspace=workspace)
```

Every `for_actor()` call in the views this MVP allowlists for PAT auth (§2)
— `ConversationListCreateView`, `ConversationMessageListView` (including its
own conversation-by-id lookup), `AgentListCreateView`, `AgentDetailView`,
`AgentCancelView`, `ApprovalRequestListCreateView`,
`ApprovalRequestTransitionView` — is routed through `scoped_for_actor()`
instead of calling `.for_actor()` directly. This is a wider touch than the
brief's original three-call-site framing (superseded above), but still
contained to exactly the view classes §2 already named — no other app's
views change at all.

**`scoped_for_actor()` must run before, not after, each view's existing
"is `workspace` present" validation.** A Codex plan-review round-3 P2
finding caught that the brief as drafted only described replacing the
`.for_actor()` call itself — but every one of these views validates that a
`workspace` query param or body field is *present* (raising a 400 if it's
missing) before ever reaching that call, precisely because a `CHANNEL_WEB`
request has no other way to know which workspace to act in. A CLI/PAT
request is expected to omit `workspace` entirely (§2: the CLI config only
carries a token and a base URL, nothing workspace-specific — the whole
point of binding a PAT to one workspace is that the client never needs to
say which one). As drafted, that omission would 400 before
`scoped_for_actor()` is ever called, breaking the CLI's basic create/list
flows. **Fix**: each view's existing "is workspace present" check must
itself first ask `core.channels.is_low_trust(request)` — if true, the
presence requirement is skipped entirely (the workspace is implied by the
bound credential, never asked of the client); if false (`CHANNEL_WEB`),
behavior is exactly what it is today, unchanged. Concretely, this means
`scoped_for_actor()` is called unconditionally, and only a `CHANNEL_WEB`
request with a still-missing `workspace` value falls through to the
existing 400 — the validation and the queryset-scoping fix are one
combined change per view, not two independent ones.

**This must happen before serializer validation too, not only at the
view's own presence check.** A Codex plan-review round-4 P2 finding caught
that the paragraph above only described the view-level check — but
`ConversationCreateSerializer`, `AgentCreateSerializer`, and
`ApprovalRequestCreateSerializer` each already declare `workspace` as a
required serializer field, and DRF runs `serializer.is_valid()` (which
enforces that requiredness) before any view code gets a chance to reason
about `is_low_trust()` at all — the 400 happens one layer earlier than the
brief's first draft accounted for. **Fix**: for a channel-bound request,
each allowlisted create view sets `request.data["workspace"] =
auth.bound_workspace_id` (overriding any client-supplied value, or
supplying it when absent) *before* constructing the serializer — the
serializer's existing required-field validation is satisfied unconditionally
without weakening it for `CHANNEL_WEB` requests, and without needing a
second, conditionally-optional serializer variant. This is the same
"resolve the bound workspace first, let existing validation run unchanged
after" shape as the presence-check fix above, applied one layer deeper.

**`PersonalAccessToken` model** (`accounts/models.py`, not a separate
`integrations` model — see the import-linter note below for why):
```python
class PersonalAccessToken(WorkspaceScopedModel, TimestampedModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    name = models.CharField(max_length=255)       # user-chosen label, e.g. "laptop cli"
    token_hash = models.CharField(max_length=64, unique=True)  # sha256 hex; raw value never stored
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)   # soft-revoke, never deleted -- audit trail
```
`workspace` (from `WorkspaceScopedModel`) is the token's one bound workspace,
validated at mint time via `Workspace.objects.for_actor(request.user,
workspace=requested_workspace_id)` (the user must actually be a member — same
check every other workspace-accepting endpoint already makes). The raw token
(`jpat_<32 url-safe random chars>`, via `secrets.token_urlsafe`) is returned
**exactly once**, in the mint response body, and never persisted or
retrievable again — standard PAT UX (GitHub/Stripe-shaped), not a novel
mechanism.

**`PersonalAccessTokenAuthentication`** (`accounts/authentication.py`), a
DRF authentication class:
- Parses `Authorization: Bearer jpat_...`. If the token doesn't start with
  the `jpat_` prefix, returns `None` immediately (does not apply — falls
  through to the next configured authenticator).
- **Never added to `DEFAULT_AUTHENTICATION_CLASSES`.** A Codex plan-review
  round-1 P1 finding caught that adding it globally would authenticate a PAT
  against *every* `IsAuthenticated` view in the codebase — `tasks/views.py`,
  `memory/views.py`, `knowledge/views.py`, and every other workspace-scoped
  surface not touched by this brief — all of which resolve workspace
  purely from `request.user` + the client-supplied query param, with no
  knowledge of `effective_workspace_id()`. A PAT bound to workspace A could
  then read/mutate workspace B on any of those endpoints, for any workspace
  its underlying user happens to belong to — defeating the one containment
  property this whole design exists to guarantee. **Fix: explicit
  per-view allowlist, not a global default.** Only the view classes this
  brief actually intends to expose to the CLI —
  `ConversationListCreateView`/`ConversationMessageListView` (assistant),
  `AgentListCreateView`/`AgentDetailView`/`AgentCancelView` (agents),
  `ApprovalRequestListCreateView`/`ApprovalRequestTransitionView` (approvals
  — the transition view needs it too, specifically so a PAT-authenticated
  decide attempt gets the deliberate channel-restriction 403 rather than a
  generic 401) — set `authentication_classes = [PersonalAccessTokenAuthentication,
  ...api_settings.DEFAULT_AUTHENTICATION_CLASSES]` explicitly. Every other
  app (`tasks`, `memory`, `knowledge`, `workspaces`, `accounts`) is
  untouched and stays JWT-only by construction — a PAT simply cannot
  authenticate there at all (401), which is a *stronger* posture than
  global-plus-per-view-opt-out: fail-closed by default, opt-in per view,
  rather than fail-open globally with per-view enforcement that's easy to
  forget on a future endpoint.
- **When listed on a view, must precede `JWTAuthentication` in that view's
  `authentication_classes`.** This is a real ordering constraint, not a
  style preference: DRF's authentication loop stops at the first
  authenticator that raises `AuthenticationFailed` — it does not fall
  through to try the next one. `rest_framework_simplejwt`'s
  `JWTAuthentication.authenticate()` raises `InvalidToken` (an
  `AuthenticationFailed` subclass) for any string that isn't JWT-shaped —
  including a `jpat_...` token — which means if `JWTAuthentication` ran
  first on one of the allowlisted views, every PAT-authenticated request to
  it would be rejected before our own authenticator ever saw it. Call this
  out explicitly in a code comment at each allowlisted view, since silently
  reordering it later would reintroduce the bug with no test failing
  locally (it only manifests with a real PAT).
- On success: hashes the token, looks up a matching, non-revoked
  `PersonalAccessToken` whose `user.is_active` is `True`, stamps
  `last_used_at`, returns `(token.user, ChannelPrincipal(channel=CHANNEL_CLI,
  bound_workspace_id=token.workspace_id))`. **The `is_active` check is
  required, not optional** — a Codex plan-review round-4 P1 finding caught
  that the brief as drafted authenticated `token.user` unconditionally,
  meaning a deactivated account could keep using a CLI credential
  indefinitely unless someone remembered to separately revoke every one of
  its tokens. `JWTAuthentication`'s own user lookup enforces this already;
  a bespoke authenticator gets nothing for free and must check explicitly.
  A deactivated user's still-unrevoked PAT is rejected exactly like a
  revoked or unknown one (401), never a partial/degraded success.
- On no match / revoked: raises `AuthenticationFailed` (401), same shape as
  a bad JWT today.
- On any other, non-allowlisted view: the token is never even parsed (the
  view's `authentication_classes` doesn't include it), so it behaves exactly
  as an unrecognized/missing credential would — 401, same as today.

**Endpoints** (`accounts/urls.py`, alongside the existing
`/api/v1/auth/*` routes — web/JWT-authenticated only, minting a token is
itself a sensitive, human-decided action, never done by a channel that's
already-low trust):
- `POST /api/v1/auth/tokens` — `{workspace, name}` → `{id, name, workspace, token, created_at}` (raw `token` only ever appears in this one response).
- `GET /api/v1/auth/tokens` — list (never includes the raw token or hash).
- `POST /api/v1/auth/tokens/<id>/revoke`.

**All three filter by `user=request.user`, not only workspace membership.**
A Codex plan-review round-5 P2 finding caught that `PersonalAccessToken`
inherits `WorkspaceScopedModel`, so a naive `PersonalAccessToken.objects.
for_actor(request.user, workspace=...)` would return *every* token in any
workspace the requester belongs to — in a company workspace with more than
one member, that lets one member list another member's token metadata (name,
last-used, revoked status) or even revoke a token they don't own. A PAT is
a per-user credential, not a shared workspace resource, unlike every other
`WorkspaceScopedModel` this codebase has shipped so far (which are
genuinely workspace-shared, e.g. a `Task` or `Memory` any member with the
right role may act on). **Fix**: list/revoke additionally filter
`user=request.user` — `PersonalAccessToken.objects.for_actor(request.user,
workspace=...).filter(user=request.user)` — so only the requester's own
tokens are ever visible or revocable, regardless of who else shares the
workspace.

**Approvals channel restriction** (`approvals/services.py`): `approve_request`
and `reject_request` gain a `channel: str` parameter (passed by
`approvals/views.py` via `core.channels.request_channel(request)`). Inside
the same `select_for_update()`-locked block that already runs
`_check_requester_not_decider` (`services.py:146-148`), add: if
`channel != CHANNEL_WEB`, raise `ApprovalPermissionDenied` — same exception,
same 403 shape the existing self-approval-denial case already produces, just
a second reason it can fire. **Default-deny by channel identity, not by
denylisting known-untrusted channels**: only `CHANNEL_WEB` is ever accepted
for a decision; any channel value this MVP didn't anticipate (a future
integration) is untrusted by default until someone deliberately extends this
one check — the safer failure direction for a hard rule ADR-004 says "no
configuration may relax."

**`Message.channel`** (`assistant/models.py`): new `CharField` (default
`"web"`, choices `web/cli/telegram`), stamped on **both** the user message
and the assistant's reply for that turn — not only the former. A Codex
plan-review round-3 P2 finding caught that the brief as drafted only
described stamping the user-authored `Message` row inside
`ConversationMessageListView.post`, but the assistant's own reply is
persisted later and separately, inside `stream_assistant_reply()` /
`_persist_assistant_message()` (`assistant/services.py`) — a CLI-originated
turn's assistant reply would silently keep the field's `"web"` default,
inconsistent with the Telegram relay path (§4), which was already specified
to stamp both of its own rows explicitly. **Fix**: `channel` is threaded as
a parameter from `ConversationMessageListView.post`'s
`core.channels.request_channel(request)` call, through
`build_gateway_stream(...)`/`stream_assistant_reply(...)`, down to
`_persist_assistant_message(...)`, so a turn's user message and its
assistant reply always carry the same channel value — one resolved-once
value threaded through the one existing call chain, not two independently
stamped fields that could drift apart. This directly mirrors the audit-trail
pattern already established for `Task.source` and `Agent.skill_name`:
months later, "which channel sent this" is answerable from the row itself,
not inferred. It's also what makes the approvals restriction's *input*
(request channel) and the chat pipeline's *audit record* (message channel)
the same one concept threaded through two places, not two
independently-invented ones.

**Import-linter**: add `"integrations"` to `root_packages` and to both
existing "kernel apps" shared contracts (core-independence,
no-Django-config-import). **Exactly one new directional edge is allowed:
`integrations -> assistant`** (needed for §4's relay; one-way, mirroring the
existing `agents -> tools` shape) **plus `integrations -> accounts` and
`integrations -> workspaces`** (needed for workspace-membership validation
at link time — the same already-open direction every other kernel app
uses). New forbidden-import contracts, precisely stated to avoid the
self-contradiction a Codex plan-review round-4 P2 finding caught in an
earlier draft (which said `integrations` may import `assistant` in one
sentence and then, read literally, forbade any edge between them at all
two sentences later): `assistant` must not import `integrations`;
`approvals` must not import `integrations`; `agents` must not import
`integrations`; `integrations` must not import `approvals` or `agents`
(it needs neither); `ai_core` must not import `integrations` (add to the
existing business-app forbidden list).

**Why `PersonalAccessToken` lives in `accounts`, not `integrations`**: a
Codex plan-review round-2 P1 finding caught a direct contradiction in the
brief's first draft — `PersonalAccessTokenAuthentication` was specified to
live in `integrations.authentication`, but §2 also requires
`assistant`/`agents`/`approvals`'s views to set it as their
`authentication_classes`, which means those modules must import it. That's
an `assistant -> integrations` (etc.) edge, directly forbidden by the
contract two paragraphs up — a real contradiction that would fail
import-linter in CI, not just an inconsistency on paper. Since `integrations
-> assistant` is independently required for §4's Telegram relay, both
directions can't be true at once without a cycle. The fix is not a new
exception to the one-way rule; it's recognizing that a PAT is fundamentally
an authentication credential — the CLI's equivalent of the JWT
`accounts.AuditedLoginView` already mints — not a Telegram-specific concern,
so it belongs where `accounts.User` and its other auth mechanics already
live. `assistant`/`agents`/`approvals` already import `accounts` everywhere
they reference `User` — routing `PersonalAccessTokenAuthentication` through
that same, already-open edge adds no new dependency direction at all.
`integrations` keeps exactly what's Telegram-specific (`TelegramLinkCode`,
`LinkedTelegramChat`, the webhook, the relay task) and its one-way
`-> assistant` edge for the relay, with nothing importing it back.

### 2. CLI channel

Given §1's workspace-fixation + PAT authentication, the CLI channel adds
**no new business-logic code path** — a CLI-authenticated request to
`/api/v1/ai/conversations/.../messages`, `/api/v1/agents`, or
`/api/v1/approvals/requests` (request-only; decisions are blocked by §1's
channel check) is handled by the exact same views the web app already calls.
What this branch actually delivers:

- A minimal CLI client (`backend/integrations/cli/friday_cli.py`, invoked via
  `uv run python -m integrations.cli.friday_cli`): reads a token + base URL
  from `~/.config/friday/cli.toml` (or `FRIDAY_CLI_TOKEN`/`FRIDAY_CLI_URL`
  env vars), sends a one-off message to a conversation (creating one if none
  is configured) and prints the streamed reply to stdout. Deliberately thin —
  a script that proves the channel works end-to-end, not a polished terminal
  UI (that's explicitly out of scope, below).
- A `manage.py create_dev_pat <email> <workspace-name>` management command
  (`accounts/management/commands/`, alongside the `PersonalAccessToken`
  model it mints) for local/dev bootstrapping without needing the frontend
  UI from §3 first (mirrors how other dev-only bootstrapping already works,
  e.g. `seed_ai_defaults`).

### 3. Frontend: token + Telegram-link settings UI

A new "Integrations" section (page or panel — implementation branch's call)
reachable from the existing nav:
- **Personal access tokens**: list existing tokens (name, workspace,
  created/last-used/revoked), a "Create token" form (name + workspace
  picker), showing the raw token **exactly once** in a copyable, dismiss-once
  banner immediately after creation (never re-displayed, never fetchable
  again — the UI must make this one-time-ness obvious, not just true), and a
  "Revoke" action per token.
- **Telegram link**: a "Link Telegram" button (workspace picker) that calls
  `POST /api/v1/integrations/telegram/link-code` (§4) and displays the
  returned code with instructions ("Message @<bot username> with `/link
  <code>`"), plus the code's expiry countdown. Shows current link status
  (linked chat exists / not linked) with an "Unlink" action if already
  linked.

### 4. Telegram bot

**Models** (`integrations/models.py`):
```python
class TelegramLinkCode(WorkspaceScopedModel, TimestampedModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    code = models.CharField(max_length=32, unique=True)
    expires_at = models.DateTimeField()      # short-lived, e.g. 10 minutes
    consumed_at = models.DateTimeField(null=True, blank=True)

class LinkedTelegramChat(WorkspaceScopedModel, TimestampedModel):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    chat_id = models.CharField(max_length=64, unique=True)  # Telegram chat id
    conversation = models.ForeignKey("assistant.Conversation", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "workspace"], name="unique_telegram_link_per_user_workspace")
        ]
```
**`(user, workspace)` uniqueness, not just `chat_id` uniqueness.** A Codex
plan-review round-4 P2 finding caught that the model as drafted only made
`chat_id` unique — nothing stopped the same Friday user from linking the
same workspace from two different private Telegram chats (e.g. relinking
from a new phone without unlinking the old one first), which would leave
two `LinkedTelegramChat` rows for one `(user, workspace)` pair and make the
§4 status/unlink endpoints ambiguous (which row do they mean?). **Fix**: the
model adds a `UniqueConstraint` on `(user, workspace)`; the linking flow
(below) treats consuming a link code while a row already exists for that
pair as a **replacement**, not an error or a second row — the old
`LinkedTelegramChat` is deleted (its `Conversation`/`Message` history is
untouched, only the link row goes) and a fresh one is created pointing at
the new `chat_id`, exactly like re-running the flow from scratch.

One `Conversation` per linked chat, created at link time — the simplest
correct mapping (no in-bot "new conversation" command in scope; see below).

**Status and unlink endpoints** (web/JWT only): a Codex plan-review round-3
P2 finding caught that §3's frontend requirement ("shows current link
status... with an Unlink action") had no backing API in this section at
all — branch 4 would have had to invent undocumented surface to build it.
Two endpoints close this:
- `GET /api/v1/integrations/telegram/link-status?workspace=<id>` →
  `{linked: bool, chat_id: str | null, linked_at: str | null}` (looks up
  `LinkedTelegramChat` for `(request.user, workspace)` — at most one per
  pair, since linking again with a fresh code while already linked replaces
  rather than duplicates, see below).
- `DELETE /api/v1/integrations/telegram/link?workspace=<id>` — deletes the
  `LinkedTelegramChat` row for that `(user, workspace)`. Hard delete is fine
  here (unlike a `PersonalAccessToken`'s soft-revoke): the row carries no
  audit value once gone — the `Conversation` and its `Message` rows
  (already channel-stamped, per above) persist independently and remain the
  actual audit trail of what was said. Re-linking afterward mints a fresh
  `TelegramLinkCode` and, on consumption, a fresh `LinkedTelegramChat` (and
  a fresh `Conversation` — the old one is simply no longer referenced by any
  active link, not deleted).

**Linking flow**: `POST /api/v1/integrations/telegram/link-code` (web/JWT
only, `{workspace}` body) creates a `TelegramLinkCode`, returns `{code,
expires_at}`. The user DMs the bot `/link <code>`. **Every inbound update is
checked against `message.chat.type == "private"` before anything else runs**
— a Codex plan-review round-1 P1 finding caught that the brief as originally
drafted declared group chats out of scope in prose but never actually
enforced it: if the bot were added to a group, the same `/link <code>`
handling would accept the group's own `chat_id`, and since the relay path
identifies a linked chat purely by `chat_id`, every member of that group
would then be able to send messages through the linking user's workspace —
group chats have no per-sender identity in this design at all, only a
per-chat one. **Fix, non-negotiable**: `process_telegram_update` checks
`chat.type` first; anything other than `"private"` consumes no link code,
relays no message, and creates no `LinkedTelegramChat` — silently ignored,
or at most a one-time "this bot only works in a direct message" reply, never
a partial/best-effort handling. The **webhook view**
(`POST /api/v1/integrations/telegram/webhook`, `AllowAny` — Telegram is not a
Friday user and holds no JWT) verifies the inbound request is actually from
Telegram via the `X-Telegram-Bot-Api-Secret-Token` header, which must equal
`settings.TELEGRAM_WEBHOOK_SECRET` (a secret chosen by us and registered with
Telegram's `setWebhook` call, not the bot API token itself — Telegram's own
documented mechanism for verifying webhook authenticity; reject with 403
before touching the database or any business logic if it doesn't match,
mirroring this codebase's existing "fail loud before doing anything" boot
checks). On a verified request, the view does the minimum possible work —
parse the update's shape, then **enqueue a Celery task with the raw update
payload and return `HttpResponse(status=200)` immediately** — and nothing
else runs synchronously inside the webhook request.

**Why this must be async, not handled inline**: Telegram expects a fast
acknowledgement and will retry webhook delivery on a slow or failed response;
the actual work (LLM gateway call via `classify_intent`/
`build_gateway_stream`, which can take seconds) must never run inside that
response cycle, or a slow gateway call would cause Telegram to redeliver the
same update, double-processing it. `integrations/tasks.py::process_telegram_update`
(a Celery task, mirroring `agents/tasks.py`'s existing
`soft_time_limit`-bounded shape) does the real work:
- If the message text matches `/link <code>`: **atomically** claim an
  unexpired, unconsumed `TelegramLinkCode` — inside a transaction, via
  `TelegramLinkCode.objects.select_for_update().get(code=code,
  expires_at__gt=now, consumed_at__isnull=True)` (the same
  `select_for_update()`-locked-claim shape `agents/execution.py` already
  uses for step claiming), or an equivalent conditional
  `UPDATE ... WHERE consumed_at IS NULL` — before creating the
  `LinkedTelegramChat` and marking `consumed_at`. A Codex plan-review
  round-6 P2 finding caught that the brief's original "look up, then create,
  then mark consumed" phrasing was a plain read-then-write with no locking:
  two concurrent `/link <code>` deliveries (a genuine possibility under
  Telegram's at-least-once redelivery) could both observe the code as
  unconsumed before either write lands, letting a one-time code be consumed
  twice or racing two workers into the replacement path against the same
  `(user, workspace)` simultaneously. The claim and the consequent
  create/replace both happen inside the one locked transaction, so a second
  concurrent attempt simply fails to claim the already-consumed code and
  falls through to the "invalid or expired code" reply below — never a
  silent double-link. On no claimable match, reply with a plain "invalid or
  expired code" message — never creates a link, never guesses.
- Otherwise: look up a `LinkedTelegramChat` by `chat_id`. If none, reply
  asking the user to link first (no anonymous chat — every relayed message
  must resolve to a real `(user, workspace)` pair, per ADR-001). If found,
  call the same `assistant.services` functions the web endpoint uses
  (`classify_intent`, `build_gateway_stream`, then persist both the user and
  assistant `Message` rows with `channel="telegram"`) against the linked
  `Conversation`, **consuming the SSE generator fully server-side** (Telegram
  has no practical token-by-token streaming UX) and sending the complete
  final text via `telegram_client.py`'s `send_message()`, which **splits
  the text into multiple `sendMessage` calls if it exceeds Telegram's
  4096-character message limit** (sequential calls, same `chat_id`) rather
  than sending one oversized call that Telegram would simply reject. A
  Codex plan-review round-6 P2 finding caught that the brief as drafted
  assumed a single `sendMessage` call always suffices — untrue for any
  normal long-form assistant answer, which routinely exceeds that limit;
  `send_message()` is the one place this chunking logic lives, so every
  Telegram reply (chat, link confirmation, error messages) goes through it
  uniformly rather than each call site reimplementing length-checking.
- A task_draft/tool_request draft produced by this relay degrades to a
  **read-only** plain text description ("I'd create a task 'X' — send this
  request from the web chat if you'd like me to actually do it") — Telegram
  never confirms a draft itself in this MVP, and **neither does any later
  web-UI action on that specific draft**. A Codex plan-review round-4 P2
  finding caught that the brief's first draft additionally specified a
  `/confirm`-style text command wired to the existing draft-confirm
  endpoint, but that endpoint performs a real, side-effecting action
  (creating a `Task` or an `ApprovalRequest`) — unlike every other
  Telegram-relayed action in this MVP, it is **not** naturally idempotent
  under Telegram's at-least-once webhook redelivery, and a redelivered
  `/confirm` could create the same task or approval request twice. That
  round's fix was to point the user at "the existing web UI" instead — but a
  round-6 P2 finding caught that this doesn't actually work either: the web
  app's draft-confirm card only exists for the duration of the one live SSE
  stream that produced it (`assistant/views.py`'s `draft` event, consumed
  once by the browser tab that sent that message); there is no mechanism
  anywhere in the codebase to reopen a past conversation turn and confirm a
  draft from it after the fact, since chat history only ever renders
  persisted `Message.content`, never reconstructs an action from
  `Message.intent_payload`. Building that would be persisted-draft
  reconstruction plus a new confirm API — a real, separately-scoped feature,
  not something this MVP should absorb as a side effect of adding a chat
  channel. **Fix, final for this MVP**: a Telegram-relayed task_draft/
  tool_request is pure description, full stop — there is no confirm path
  for it in *either* channel. If the user wants to actually act on it, they
  re-issue the same request from the web chat, where the existing,
  already-safe live-stream draft flow (unchanged by this MVP) handles it
  exactly as it does today. This also means the "no `update_id`
  deduplication" simplification (Out of scope, below) is now actually true
  for every relayed action, not contradicted by one side-effecting
  exception.

**Secrets**: `TELEGRAM_BOT_TOKEN` (used only server-side by
`telegram_client.py` to call the Bot API) and `TELEGRAM_WEBHOOK_SECRET` (used
only to verify inbound webhook calls) are both new env vars — added to
`.env.example` with placeholder values, never committed as real values, per
CLAUDE.md's secrets rule. A `manage.py register_telegram_webhook` command
does the one-time `setWebhook` call against Telegram's API using these
settings — an operator action, not something Celery beat runs periodically.

### 5. Periodic cleanup

`integrations/tasks.py::sweep_expired_link_codes` — a periodic Celery beat
task (added to `config/celery.py`'s existing `beat_schedule`, mirroring
`approvals.tasks.sweep_expired_approval_requests`'s exact shape) that deletes
(hard-delete is fine here — a `TelegramLinkCode` carries no audit value once
expired and unconsumed, unlike an `ApprovalRequest` or `PersonalAccessToken`)
`TelegramLinkCode` rows past `expires_at` and still unconsumed.

## Out of scope (explicit)

- **Gmail/Calendar/Drive, GitHub, local folder sync** — the other three
  candidates in `docs/integrations/README.md`. Each needs its own OAuth/API
  design and is a separate, later `integrations` MVP, not folded in here.
- **Telegram group chats** — only 1:1 DM with the bot is supported. A group
  chat has no single obvious `(user, workspace)` mapping and raises its own
  moderation/multi-user questions this MVP doesn't need to answer.
- **Rich Telegram UX** — inline keyboards/buttons for confirming drafts,
  markdown-formatted replies, file/voice message handling. Plain text in,
  plain text out.
- **Confirming a task_draft/tool_request draft that was surfaced via
  Telegram — in either channel.** See §4's round-4 and round-6 fixes: not a
  Telegram command (side-effecting, not safely idempotent under at-least-once
  redelivery) and not a later web-UI action either (the web app's
  draft-confirm mechanism only exists for the live SSE stream that produced
  it — there is no persisted-draft reconstruction feature in this codebase
  to reopen and act on a past turn, and building one is out of scope here).
  A Telegram-relayed draft is pure description; acting on it means
  re-issuing the same request from the web chat.
- **Persisted/reconstructable draft confirmation from chat history** (for
  any channel, not just Telegram) — a real feature (rebuild a
  task_draft/tool_request from `Message.intent_payload` and expose a confirm
  API for it outside the live streaming turn that created it), but one this
  MVP's Telegram work surfaced as missing rather than one it needs to build.
- **Multi-workspace-per-link** — one `LinkedTelegramChat` or
  `PersonalAccessToken` is bound to exactly one workspace for its lifetime;
  switching means unlinking/revoking and relinking/reminting, never an
  in-place workspace change.
- **`update_id`-based Telegram delivery deduplication.** A redelivered update
  would at worst duplicate one relayed message and one gateway call (a cost,
  not a correctness bug, since each relay is otherwise idempotent-shaped —
  no double side effects beyond the LLM call itself) — acceptable for a v1
  on the current single-worker topology; revisit if it's ever observed in
  practice.
- **CLI OAuth device-code flow, token expiry/rotation, or scoped
  permissions narrower than "everything the user's role in that workspace
  already allows."** A PAT is a bearer credential with the same effective
  permissions as the user it belongs to (minus the channel restriction on
  approval decisions) — not a narrower-scoped API key. Fine-grained PAT
  scopes are a real, separately-justified feature, not part of this MVP.
- **Extending the channel-trust check to request *creation* (only
  *decisions* are restricted).** A low-trust channel proposing a
  `tool_request` or task draft is unchanged behavior — writer-role gated,
  exactly as today; restricting creation too isn't what
  `docs/integrations/README.md` asked for and would make Telegram/CLI unable
  to do anything approval-adjacent at all, not just unable to decide.
- **A Telegram "new conversation" command.** One conversation per linked
  chat for the lifetime of the link; if this proves limiting in practice,
  it's a small, separately-scoped follow-up.

## Branch plan

| # | Branch | Contents |
|---|---|---|
| 1 | `feat/mvp9-channel-foundation` | **Done (2026-07-13, merged to main `12ad4f9`).** `integrations` app scaffold (Telegram-only); `core/channels.py` (`ChannelPrincipal`, `request_channel`, `is_low_trust`, `scoped_for_actor`); `PersonalAccessToken` model + migration (`accounts`); `PersonalAccessTokenAuthentication` (`accounts`), explicitly allowlisted (never global) onto exactly the assistant/agents/approvals view classes named in §1, each with `PersonalAccessTokenAuthentication` ordered before `JWTAuthentication`; PAT mint/list/revoke endpoints (`accounts`); every `for_actor()` call in those allowlisted views routed through `scoped_for_actor()`, with each view's existing "workspace required" validation updated to skip that requirement for low-trust channels; `approvals.services` channel restriction on `approve_request`/`reject_request` + tests; `Message.channel` field + migration + stamping threaded through both the user-message and assistant-reply persistence paths in `assistant/services.py`; import-linter contracts. 3 rounds of Codex code review, 2 real P2 fixes. |
| 2 | `feat/mvp9-cli-channel` | **Done (2026-07-13, merged to main `6d69147`).** CLI client script (`integrations/cli/friday_cli.py`); `create_dev_pat` management command; tests exercising a PAT-authenticated request against the existing chat/agents/approvals endpoints (including a request that omits `workspace` entirely, and the negative case: a PAT-authenticated approve/reject attempt is rejected). 4 rounds of Codex code review, 4 real P2 fixes (ambiguous workspace-name match, missing `client_local_date`, a whitespace-mutation bug, a missing `DEBUG` gate). |
| 3 | `feat/mvp9-telegram-bot` | **Done (2026-07-14, merged to main `db00dee`).** `TelegramLinkCode`/`LinkedTelegramChat` models + migration; link-code mint endpoint; link-status/unlink endpoints; webhook view (secret verification, `chat.type == "private"` check, fast-ack, enqueue); `telegram_client.py` (mockable HTTP wrapper); `process_telegram_update` Celery task (link flow + relay flow); `sweep_expired_link_codes` periodic task + beat wiring; `register_telegram_webhook` management command; `.env.example` additions; tests (webhook secret rejection, group-chat rejection, link-code consume/expire/reuse, link-status/unlink, relay-through-existing-pipeline, `Message.channel="telegram"` stamped correctly, no synchronous gateway call inside the webhook request). 4 rounds of Codex code review: round 1 escalated 1 new P1 to the user (relay-time link revalidation) + 1 real P2 (throttling); round 2, 1 real P2 (link flow bypassed the audited `create_conversation` service); round 3, 1 real P2 (link-code consumption not revalidating access the same way relay already did); round 4, 2 real P2s (webhook accepting traffic with no bot token, unbounded outbound HTTP call) — approved directly by the user without a 5th round. A recurring `update_id`-deduplication finding was correctly recognized each time as a re-raise of an already-decided, documented scope exclusion. |
| 4 | `feat/mvp9-frontend-settings` | **Done (2026-07-14, merged to main `65ea9fa`).** Integrations settings UI (§3): token list/create/revoke, Telegram link-code request + status + unlink; frontend types/BFF routes/component tests. 4 rounds of Codex code review, all P2s, all fixed: raw PAT token retained in list state after banner dismissal; a link-code response race after a mid-flight workspace switch; the same race pattern in the Unlink flow; the reveal banner's "Copied" state carrying over to a newly created token — the user was consulted directly after the third consecutive round with a new finding and chose to run one more round before merging. |
| 5 | `feat/mvp9-e2e-docs` | **Done (2026-07-14, merged to main `7780919`).** Playwright e2e (PAT-authenticated chat round trip against the real Docker stack, including the channel-restriction invariant proven live: a PAT-authenticated approve attempt 403s, the same request approved over the web channel 200s; Telegram flow via real webhook POSTs against the real backend, asserted on DB-observable state — see Risks for the accepted one-real-outbound-call trade-off); `docs/integrations/README.md` fleshed out from a stub into a real guide; `MASTER_PLAN.md` new §Y section + roadmap line. Codex code review: round 1, 0 findings, direct APPROVE. |

**MVP 9 COMPLETE — all 5 branches merged to main, all Codex review loops closed.**

**Config enumerations to touch (branch 1)**: `backend/pyproject.toml` —
add `"integrations"` to both `testpaths` and import-linter `root_packages`
(mirroring every prior MVP's kernel-app-onboarding pattern, e.g. MVP8's
`"skills"` addition). A Codex plan-review round-7 P2 finding caught that
the brief's import-linter contracts (§1) were specified without this
companion `testpaths` entry — the same explicit-allowlist `pytest`
configuration this repo already uses means a new app's `tests/` package is
silently never collected without it, so branch 1's own new tests would not
actually run under the default `pytest`/CI invocation until this is added.

## Test strategy

- **Channel-trust primitive**: `request_channel`/`is_low_trust`/
  `scoped_for_actor` unit tests for both web (default) and channel-bound
  (`ChannelPrincipal`) cases — including a by-id lookup (no workspace
  parameter at all) and a body-supplied `workspace` mismatch, not only the
  query-param case, to prove the round-2 P1 fix's coverage claim.
- **PAT authentication**: valid token authenticates and stamps
  `last_used_at`; revoked/unknown/malformed token is rejected (401); a
  non-`jpat_`-prefixed value returns `None` and falls through to
  `JWTAuthentication` unchanged (proves the ordering fix, §1, actually
  works, not just that it's plausible).
- **Workspace fixation**: a PAT-authenticated request supplying a
  *different* `?workspace=` (or body `workspace`) than the one it's bound to
  still only ever acts in its bound workspace; a PAT-authenticated by-id
  request (detail/cancel/transition) for an object that exists but belongs
  to a *different* workspace than the one the PAT is bound to gets a 404,
  even though the underlying user is a member of that other workspace too
  (proves the round-2 P1 fix — the id-lookup path, not just the
  query-param/body override).
- **PAT allowlist containment**: a valid PAT presented against a
  non-allowlisted endpoint (`tasks`, `memory`, `knowledge`, `workspaces`,
  `accounts`) is rejected exactly as a missing/absent credential would be
  (401) — proves the round-1 P1 fix (explicit per-view allowlist, never
  `DEFAULT_AUTHENTICATION_CLASSES`) actually contains the credential rather
  than just being described as doing so.
- **Approvals channel restriction**: a PAT-authenticated (or otherwise
  channel-bound) actor with a curator role attempting `approve`/`reject`
  is rejected with the same `ApprovalPermissionDenied` shape the existing
  self-approval test asserts; a web/JWT-authenticated curator's decision is
  unaffected (regression guard — this MVP must not break MVP 5's existing
  approval flow for the one channel that already worked).
- **Message.channel audit**: a CLI-sent chat message persists
  `channel="cli"`; a Telegram-relayed message persists `channel="telegram"`;
  existing web-sent messages still persist `channel="web"` (regression
  guard against every prior MVP's chat tests). Critically, **both** the
  user message and the assistant's reply for the same turn carry the
  identical channel value (proves the round-3 P2 fix — threading `channel`
  through `_persist_assistant_message`, not only the user-message call
  site).
- **PAT requests without an explicit `workspace`**: a CLI/PAT-authenticated
  create or list request that omits `workspace` entirely still succeeds,
  scoped to the token's bound workspace, rather than 400ing on a missing
  parameter (proves the round-3 P2 fix — the presence check itself must
  defer to `is_low_trust(request)`, not just the `for_actor()` call).
- **Telegram link status/unlink**: `GET .../link-status` reflects `linked:
  false` before linking and `linked: true` with the correct `chat_id` after;
  `DELETE .../link` removes the `LinkedTelegramChat` row but leaves its
  `Conversation`/`Message` rows intact; re-linking afterward creates a fresh
  `LinkedTelegramChat` (proves the round-3 P2 fix's endpoints exist and
  behave as specified, not just that they're named in the brief).
- **Telegram webhook security**: a request missing or with a wrong
  `X-Telegram-Bot-Api-Secret-Token` is rejected (403) before any DB write or
  Celery enqueue; a correctly-secreted request enqueues the task and returns
  200 without running the relay synchronously (assert via a monkeypatched
  Celery task, mirroring how other apps test `.delay()` call sites without
  a live broker).
- **Telegram link flow**: valid unexpired code creates exactly one
  `LinkedTelegramChat` + `Conversation` and marks the code consumed; an
  expired or already-consumed code is rejected and creates nothing; a
  message from an unlinked `chat_id` gets a "link first" reply and creates
  no `Message`/`Conversation` rows; an update whose `chat.type` is not
  `"private"` (a group/supergroup) never consumes a link code and never
  creates a `LinkedTelegramChat`, even with an otherwise-valid code (proves
  the round-1 P1 fix).
- **Telegram relay parity**: a relayed message produces the identical
  `classify_intent`/`ModelUsage` accounting a web-sent message with the same
  content would (reusing MVP 6's existing router test fixtures against the
  relay path, not a new parallel set of assertions).
- **Self-modification / no-new-bypass regression**: neither the CLI nor
  Telegram code paths call `approvals.services.approve_request`/
  `reject_request` themselves anywhere (the relay/CLI can only ever reach
  the *request-creation* side) — a static test alongside the existing
  `test_self_modification.py` suite.

## Risks / notes

- **This MVP's core safety property is the channel-decision restriction,
  not the two channels themselves.** If plan-review or implementation finds
  a path where a Telegram- or CLI-authenticated actor can approve/reject an
  `ApprovalRequest` — directly, or indirectly through some endpoint this
  brief didn't enumerate — that is a scope violation against ADR-004, flag
  it back rather than resolving it unilaterally, per the same standing rule
  every prior MVP's hard invariants were held to.
- **No real Telegram network calls in CI or tests.** `telegram_client.py`
  must be structured so tests substitute a fake/mock transport (mirroring
  `ai_core.FakeAdapter`'s role for LLM calls) — the real Bot API token is a
  deploy-time secret that must never be required for the test suite to pass,
  and CI must never make outbound calls to a third party.
- **PAT is a full-permission bearer credential, not a scoped API key** (see
  Out of scope) — this is a deliberate simplification for v1; if a real need
  for narrower scopes emerges, that's a separately-argued follow-up, not a
  silent addition here.
- **Webhook authenticity relies on `X-Telegram-Bot-Api-Secret-Token`
  matching a value we chose and registered via `setWebhook`** — this is
  Telegram's own documented mechanism, not a bespoke scheme; get the
  registration step (`register_telegram_webhook` management command) right
  and tested (in the sense of "the header check works," not "a real
  `setWebhook` call succeeds in CI," which it can't).
- **Open parameter/naming values**: exact URL paths under
  `/api/v1/integrations/`, the PAT prefix (`jpat_`), the exact Telegram
  `/link`/`/confirm` command text, and model/field names are proposals —
  plan-review may rename them; the *existence and shape* of each mechanism
  (channel-trust primitive, PAT auth ordering fix, async webhook relay,
  workspace fixation) is the non-negotiable part.
