"""Conformance case definitions, protocol v1 and v2 (Phase 9 brief D5
"Conformance cases"; Phase 10 brief branch 2 "Conformance cases v2").

Every case is a frame-level script a compromised server could play: the
fake gateway signs each frame with the conformance key (re-signing anything
it tampered with, so the node's later checks are what catch it) and every
time, id and nonce is fixed, so the rendered `cases/v<N>/<name>.json` files
are byte-stable and replayable by any node, including the Swift app.

A case document:

    {"name", "version" (the session's protocol version), "check" (the D5
     check it pins, or null), "description",
     "setup": {"now", "window": [{"action", "after"}], "executor":
               [{"chunks": [[stream, text, at]], "exit_code", "duration",
                 "hang", "sandbox_violation"}], "catalogue": "server" |
               "narrow", "availability": {name: value}, "read_delay"},
     "steps": [{"op": "send", "frame"} | {"op": "send_raw", "data",
               "repeat"} | {"op": "advance", "seconds"} | {"op": "tick"} |
               {"op": "disconnect"} | {"op": "kill_switch"}],
     "expect": {"frames": [matcher...], "executor_calls", ...}}

A matcher is a subset of one node frame's fields, in order; the node must
send exactly as many frames as there are matchers. `expect` may also pin
`reader_calls` (how many times a reader ran: every case whose frames are all
refusals or frame errors pins 0, so no reader runs on any refusal path),
`executor_calls`, `executor_terminated`, `windows_shown`,
`closed_reason`, `reconnect_allowed`, `absent_b64` (base64 of strings no
frame may contain: the case files never carry a raw fake secret; executor
chunks may be `{"b64": ...}` for the same reason), `backoff_max_seconds`
and `worktrees_after` (the source repo's worktree count).
Unspecified keys are not checked. Every case also checks that the node's
local policy fingerprint is unchanged and that the gateway counted no
frame errors.

The node under test is configured by the case's `setup` and the fixture
home of `nodes.reference.fixtures.factory` (repos named `~/work/acme`, ...,
and the synthetic Claude Code and Codex transcripts under `~/.claude` and
`~/.codex`). For reads, both directory grants are on unless
`setup.reader_grants` turns one off (`{"claude": false}`), and window
titles are off unless `setup.window_titles` is true (D13 item 5).
"""

import base64
import hashlib
import json
import uuid
from functools import cache
from pathlib import Path

from nodes.protocol.grants import SHARED_FIELDS, build_gated_invoke
from nodes.protocol.signing import (
    ENVELOPE_DOMAINS,
    GRANT_DOMAIN_V2,
    INVOKE_DOMAIN_V2,
    SigningKey,
    b64url_decode,
    b64url_encode,
    build_invoke,
    sign_envelope,
    sign_with_domain,
)

KEYS_PATH = Path(__file__).resolve().parent / "keys" / "conformance_keys.json"
KEYS = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
SIGNING = SigningKey.from_b64(KEYS["signing"]["private_key_b64"])
UNPINNED = SigningKey.from_b64(KEYS["unpinned"]["private_key_b64"])
PINNED = {SIGNING.key_id: SIGNING.public_key_bytes}

NODE_ID = "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b"
OTHER_NODE_ID = "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0c"
WORKSPACE_ID = "2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6d"
APPROVAL_ID = "8d7c6b5a-4e3f-4d2c-8b1a-0f9e8d7c6b5a"
BASE = 1_796_000_000  # issued_at of most frames
NOW = BASE + 5  # the node clock when a case starts

ACME_TEST = {"repo_path": "~/work/acme", "command_kind": "test"}
ACME_LINT = {"repo_path": "~/work/acme", "command_kind": "lint"}
TERMINAL = {"bundle_id": "com.apple.Terminal"}
SECRET_TAIL = "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAB7CD0"
# The corpus's own split marker keeps the provider prefix out of the source.
SECRET_PREFIX = "sk-" + "proj-"


def _uuid4(seed: str) -> str:
    digest = bytearray(hashlib.sha256(seed.encode("utf-8")).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def _nonce(seed: str) -> str:
    return b64url_encode(hashlib.sha256(("nonce:" + seed).encode("utf-8")).digest())


def _strip(signed):
    return {k: v for k, v in signed.items() if k not in ("sig", "key_id")}


def resign(envelope, key=SIGNING):
    """A compromised server re-signs whatever it changed."""
    return sign_envelope(_strip(envelope), key)


def _flip(sig: str) -> str:
    raw = bytearray(b64url_decode(sig))
    raw[0] ^= 0x01
    return b64url_encode(bytes(raw))


def plain(
    seed,
    capability,
    args,
    *,
    cls="read",
    version=2,
    ttl=None,
    issued=BASE,
    key=SIGNING,
    capability_version=1,
    nonce=None,
    node_id=NODE_ID,
):
    ttl = ttl if ttl is not None else (30 if cls == "read" else 90)
    envelope = build_invoke(
        node_id=node_id,
        workspace_id=WORKSPACE_ID,
        capability=capability,
        capability_version=capability_version,
        args=args,
        trace_id=f"conformance-{seed}"[:64],
        issued_at=issued,
        ttl_seconds=ttl,
        capability_class=cls,
        invocation_id=_uuid4("invocation:" + seed),
        message_id=_uuid4("message:" + seed),
        nonce=nonce or _nonce(seed),
        version=version,
    )
    return sign_envelope(envelope, key)


def gated(
    seed,
    capability="repo.command.run",
    args=None,
    *,
    ttl=90,
    issued=BASE,
    nonce=None,
    approval_id=APPROVAL_ID,
):
    return build_gated_invoke(
        signer=SIGNING,
        approval_id=approval_id,
        node_id=NODE_ID,
        workspace_id=WORKSPACE_ID,
        capability=capability,
        capability_version=1,
        args=dict(ACME_TEST if args is None else args),
        trace_id=f"conformance-{seed}"[:64],
        issued_at=issued,
        ttl_seconds=ttl,
        invocation_id=_uuid4("invocation:" + seed),
        message_id=_uuid4("message:" + seed),
        nonce=nonce or _nonce(seed),
        grant_id=_uuid4("grant:" + seed),
    )


def refused(code):
    return {"type": "result", "status": "refused", "refusal_code": code}


def frame_error(code):
    return {"type": "result", "status": "error", "refusal_code": code, "invocation_id": None}


def confirmation(state):
    return {"type": "confirmation", "state": state}


def _refusals_only(frames) -> bool:
    return bool(frames) and all(
        m.get("type") == "result"
        and (m.get("status") == "refused" or m.get("invocation_id", "") is None)
        for m in frames
    )


def case(name, version, *, check=None, description, steps, frames, setup=None, **expect):
    if "reader_calls" not in expect and _refusals_only(frames):
        expect["reader_calls"] = 0
    return {
        "name": name,
        "version": version,
        "check": check,
        "description": description,
        "setup": {"now": NOW, **(setup or {})},
        "steps": steps,
        "expect": {"frames": frames, **expect},
    }


def send(frame):
    return {"op": "send", "frame": frame}


def advance(seconds):
    return {"op": "advance", "seconds": seconds}


DISCONNECT = {"op": "disconnect"}
KILL_SWITCH = {"op": "kill_switch"}


# --- checks every version shares (1-12), on a read invoke ---------------


def _common_cases(version):
    v = version
    cases = []
    ok_read = plain(f"v{v}-read-ok", "apps.frontmost", {}, version=v)
    cases.append(
        case(
            f"v{v}_read_ok",
            v,
            description="A valid read invoke answers ok with the reader's output.",
            steps=[send(ok_read)],
            frames=[
                {
                    "type": "result",
                    "status": "ok",
                    "output": {"app_name": "Terminal", "bundle_id": "com.apple.Terminal"},
                }
            ],
            executor_calls=0,
            reader_calls=1,
        )
    )
    cases.append(
        case(
            f"v{v}_oversize_frame",
            v,
            check=1,
            description="A frame over 256 KiB is a frame error (too_large).",
            steps=[{"op": "send_raw", "data": "x", "repeat": 256 * 1024 + 1}],
            frames=[frame_error("too_large")],
        )
    )
    cases.append(
        case(
            f"v{v}_malformed_json",
            v,
            check=2,
            description="Malformed JSON is a frame error.",
            steps=[{"op": "send_raw", "data": "{nope"}],
            frames=[frame_error("malformed")],
        )
    )
    cases.append(
        case(
            f"v{v}_three_bad_frames_close",
            v,
            check=2,
            description="Three frame errors within 60 s close the session.",
            steps=[{"op": "send_raw", "data": "{nope"}] * 3,
            frames=[frame_error("malformed")] * 3,
            closed_reason="protocol_error",
        )
    )
    shaped = ["policy.set", "allowlist.add", "catalogue.set", "config.update", "grant.add"]
    if v == 2:
        shaped += ["sandbox.update", "recipe.add"]
    for kind in shaped:
        cases.append(
            case(
                f"v{v}_policy_shaped_{kind.replace('.', '_')}",
                v,
                check=3,
                description=f"A policy-mutation-shaped type ({kind}) is unknown_type.",
                steps=[send({"v": v, "type": kind, "id": _uuid4(kind), "policy": {"allow": "*"}})],
                frames=[frame_error("unknown_type")],
            )
        )
    cases.append(
        case(
            f"v{v}_unknown_type",
            v,
            check=3,
            description="A server -> node type outside the frozen set is unknown_type.",
            steps=[send({"v": v, "type": "cancel", "id": _uuid4("cancel")})],
            frames=[frame_error("unknown_type")],
        )
    )
    cases.append(
        case(
            f"v{v}_schema_extra_key",
            v,
            check=4,
            description="An envelope with an extra property fails the schema.",
            steps=[send({**ok_read, "extra": 1})],
            frames=[frame_error("schema")],
        )
    )
    cases.append(
        case(
            f"v{v}_unknown_key",
            v,
            check=5,
            description="An envelope signed by an unpinned key.",
            steps=[send(plain(f"v{v}-unpinned", "apps.frontmost", {}, version=v, key=UNPINNED))],
            frames=[refused("unknown_key")],
        )
    )
    flipped = {**ok_read, "sig": _flip(ok_read["sig"])}
    cases.append(
        case(
            f"v{v}_bad_signature",
            v,
            check=6,
            description="A flipped signature bit.",
            steps=[send(flipped)],
            frames=[refused("bad_signature")],
        )
    )
    changed = {**plain(f"v{v}-args-after", "git.status", {"repo_path": "~/work/acme"}, version=v)}
    changed["args"] = {"repo_path": "~/work/other"}
    cases.append(
        case(
            f"v{v}_args_changed_after_signing",
            v,
            check=6,
            description="Args changed after signing (not re-signed).",
            steps=[send(changed)],
            frames=[refused("bad_signature")],
        )
    )
    cases.append(
        case(
            f"v{v}_wrong_node",
            v,
            check=7,
            description="An envelope for another node.",
            steps=[
                send(plain(f"v{v}-wrong", "apps.frontmost", {}, version=v, node_id=OTHER_NODE_ID))
            ],
            frames=[refused("wrong_node")],
        )
    )
    cases.append(
        case(
            f"v{v}_not_yet_valid",
            v,
            check=8,
            description="issued_at beyond the 5 s skew.",
            steps=[send(plain(f"v{v}-future", "apps.frontmost", {}, version=v, issued=NOW + 6))],
            frames=[refused("not_yet_valid")],
        )
    )
    cases.append(
        case(
            f"v{v}_expired",
            v,
            check=9,
            description="expires_at in the past.",
            steps=[send(plain(f"v{v}-expired", "apps.frontmost", {}, version=v, issued=BASE - 60))],
            frames=[refused("expired")],
        )
    )
    cases.append(
        case(
            f"v{v}_ttl_too_long",
            v,
            check=10,
            description="A read TTL of 31 s.",
            steps=[send(plain(f"v{v}-ttl", "apps.frontmost", {}, version=v, ttl=31))],
            frames=[refused("ttl_too_long")],
        )
    )
    cases.append(
        case(
            f"v{v}_replay",
            v,
            check=11,
            description="The same envelope twice: the second is a replay.",
            steps=[send(ok_read), send(ok_read)],
            frames=[{"type": "result", "status": "ok"}, refused("replay")],
            reader_calls=1,
        )
    )
    cases.append(
        case(
            f"v{v}_unknown_capability",
            v,
            check=12,
            description="A capability the node does not have.",
            steps=[send(plain(f"v{v}-unknown", "shell.exec", {"cmd": "id"}, version=v))],
            frames=[refused("unknown_capability")],
        )
    )
    cases.append(
        case(
            f"v{v}_unknown_capability_version",
            v,
            check=12,
            description="A known capability at another version.",
            steps=[send(plain(f"v{v}-ver", "apps.frontmost", {}, version=v, capability_version=2))],
            frames=[refused("unknown_capability")],
        )
    )
    hash_check, schema_check = (14, 15) if v == 1 else (19, 20)
    resigned = {**plain(f"v{v}-hash", "git.status", {"repo_path": "~/work/acme"}, version=v)}
    resigned["args"] = {"repo_path": "~/work/other"}
    cases.append(
        case(
            f"v{v}_args_hash_mismatch",
            v,
            check=hash_check,
            description="Args changed, hash kept, envelope re-signed.",
            steps=[send(resign(resigned))],
            frames=[refused("args_hash_mismatch")],
        )
    )
    hostile_args = {
        "extra_key": {"repo_path": "~/work/acme", "extra": 1},
        "wrong_type": {"repo_path": 7},
        "oversize": {"repo_path": "~/" + "a" * 1100},
        "traversal": {"repo_path": "~/work/../.ssh"},
        "nul": {"repo_path": "~/work/ac\x00me"},
        "rtl_override": {"repo_path": "~/work/‮emca"},
        "shell_meta": {"repo_path": "~/work/acme;rm -rf ~"},
    }
    for label, args in hostile_args.items():
        cases.append(
            case(
                f"v{v}_args_{label}",
                v,
                check=schema_check,
                description=f"Args outside the node's own schema ({label}).",
                steps=[send(plain(f"v{v}-args-{label}", "git.status", args, version=v))],
                frames=[refused("args_schema")],
            )
        )
    long_prefixes = {"since_hours": 24, "limit": 10, "cwd_prefixes": ["~/w"] * 40}
    cases.append(
        case(
            f"v{v}_args_over_length_array",
            v,
            check=schema_check,
            description="An over-length array argument.",
            steps=[send(plain(f"v{v}-array", "claude.sessions.list", long_prefixes, version=v))],
            frames=[refused("args_schema")],
        )
    )
    cases.append(
        case(
            f"v{v}_not_granted",
            v,
            check=16 if v == 1 else 21,
            description="A capability present but not granted on the node.",
            setup={"availability": {"apps.frontmost": "not_granted"}},
            steps=[send(plain(f"v{v}-grant", "apps.frontmost", {}, version=v))],
            frames=[refused("not_granted")],
        )
    )
    cases.append(
        case(
            f"v{v}_bye_revoked_no_reconnect",
            v,
            description="bye{revoked} closes the session and stops reconnecting.",
            steps=[send({"v": v, "type": "bye", "id": _uuid4(f"bye{v}"), "reason": "revoked"})],
            frames=[],
            closed_reason="bye:revoked",
            reconnect_allowed=False,
        )
    )
    cases.append(
        case(
            f"v{v}_server_silence_reconnect",
            v,
            description="Server silence beyond 45 s drops the socket; reconnect with backoff.",
            setup={"silent": True},
            steps=[advance(46)],
            frames=[],
            closed_reason="server_silent",
            reconnect_allowed=True,
            backoff_max_seconds=1,
        )
    )
    cases.append(
        case(
            f"v{v}_disconnect_mid_invoke_no_late_result",
            v,
            description="A read in flight when the socket drops never answers.",
            setup={"read_delay": 5},
            steps=[send(ok_read), DISCONNECT, advance(10)],
            frames=[],
        )
    )
    return cases


# --- the readers (Phase 9 branch 3): fixture transcripts, scrub, prefixes --

# Fake secrets of the fixture transcripts, assembled so no source file holds
# one whole; the case files carry them base64 only (`absent_b64`).
FIXTURE_SECRETS = (
    "sk-" + "proj-FixtureFAKE0123456789abcdefGHIJKLmnopQR",
    "ghp_" + "FixtureFAKE0123456789abcdefGHIJKLmn",
    "AKIA" + "FIXTUREFAKE12345",
    "Fixture" + "Pass2026x",
    "owner.fixture@example.com",
)
# Text the readers must never return: tool results, file contents, reasoning,
# summaries, meta and sidechain lines, harness markers, unknown line kinds.
NEVER_RETURNED = (
    "TOOL-RESULT-PHRASE",
    "FILE-CONTENT-PHRASE",
    "FUNCTION-OUTPUT-PHRASE",
    "REASONING-PHRASE",
    "SUMMARY-PHRASE",
    "META-PHRASE",
    "SIDECHAIN-PHRASE",
    "MARKER-PHRASE",
    "UNKNOWN-KIND-PHRASE",
)


def _absent(*texts):
    return [base64.b64encode(t.encode("utf-8")).decode("ascii") for t in texts]


def _ok(output):
    return [{"type": "result", "status": "ok", "truncated": False, "output": output}]


_ERROR = [{"type": "result", "status": "error", "refusal_code": None, "output": None}]

ACME_SESSION_ROW = {
    "session_id": "cc-acme-1",
    "cwd": "~/work/acme",
    "started_at": 1_795_982_400,
    "last_activity_at": 1_795_982_520,
    "message_count": 4,
    "model": "claude-opus-4-5",
    "status_hint": "idle",
}
FRIDAY_SESSION_ROW = {
    "session_id": "cc-friday-1",
    "cwd": "~/code/friday",
    "started_at": 1_795_999_200,
    "last_activity_at": 1_795_999_800,
    "message_count": 2,
    "model": "claude-sonnet-4-5",
    "status_hint": "active",
}
LAST_ACME_TURN = {
    "role": "assistant",
    "at": 1_795_982_520,
    "text": "Done: the test now waits for the clock. Ping [PII:email] if it fails again.",
    "tool_calls": 0,
    "tool_results": 0,
}
ACME_TURNS = [
    {
        "role": "user",
        "at": 1_795_982_400,
        "text": "Fix the flaky invoice test in acme.",
        "tool_calls": 0,
        "tool_results": 0,
    },
    {
        "role": "assistant",
        "at": 1_795_982_410,
        "text": "I will look at the test first.",
        "tool_calls": 2,
        "tool_results": 2,
    },
    {
        "role": "user",
        "at": 1_795_982_460,
        "text": "The staging key is [SECRET:openai_key], use it for the smoke run.",
        "tool_calls": 0,
        "tool_results": 0,
    },
    LAST_ACME_TURN,
]
CODEX_ACME_TURNS = [
    {
        "role": "user",
        "at": 1_795_989_660,
        "text": "Bump the acme API client; the token is [SECRET:github_token].",
        "tool_calls": 0,
        "tool_results": 0,
    },
    {
        "role": "assistant",
        "at": 1_795_989_720,
        "text": "Bumped the client to 2.1.",
        "tool_calls": 1,
        "tool_results": 1,
    },
]
WINDOWS = [
    {
        "bundle_id": "com.apple.Terminal",
        "app_name": "Terminal",
        "window_count": 2,
        "on_screen": True,
    },
    {"bundle_id": "com.apple.Safari", "app_name": "Safari", "window_count": 1, "on_screen": True},
    {"bundle_id": "com.apple.Notes", "app_name": "Notes", "window_count": 0, "on_screen": False},
]


def _read_case(name, capability, args, frames, *, description, setup=None, **expect):
    return case(
        f"v1_read_{name}",
        1,
        description=description,
        setup=setup,
        steps=[send(plain(f"v1-read-{name}", capability, args, version=1))],
        frames=frames,
        **expect,
    )


def _reader_cases():
    return [
        _read_case(
            "sessions_list_cwd_prefixes",
            "claude.sessions.list",
            {"since_hours": 24, "limit": 50, "cwd_prefixes": ["~/work/acme"]},
            _ok({"sessions": [ACME_SESSION_ROW]}),
            description=(
                "cwd_prefixes keeps only rows under the prefix at a segment boundary "
                "(~/work/acme-secret and ~/code/friday are skipped before parsing)."
            ),
            reader_calls=1,
            absent_b64=_absent("acme-secret", "cc-friday-1"),
        ),
        _read_case(
            "sessions_list_since_hours",
            "claude.sessions.list",
            {"since_hours": 1, "limit": 50},
            _ok({"sessions": [FRIDAY_SESSION_ROW]}),
            description="since_hours drops sessions idle longer; activity within 300 s is active.",
            reader_calls=1,
        ),
        _read_case(
            "session_detail_scrubbed",
            "claude.sessions.read",
            {"session_id": "cc-acme-1", "last_turns": 20},
            _ok({"session_id": "cc-acme-1", "turns": ACME_TURNS}),
            description=(
                "A session's turns: text scrubbed node-side, tool calls and results as counts "
                "only, never tool results, file contents, meta, sidechain or summary lines."
            ),
            reader_calls=1,
            absent_b64=_absent(*FIXTURE_SECRETS, *NEVER_RETURNED),
        ),
        _read_case(
            "session_detail_last_turns",
            "claude.sessions.read",
            {"session_id": "cc-acme-1", "last_turns": 1},
            _ok({"session_id": "cc-acme-1", "turns": [LAST_ACME_TURN]}),
            description="last_turns keeps the newest turns.",
            reader_calls=1,
        ),
        _read_case(
            "session_not_found",
            "claude.sessions.read",
            {"session_id": "cc-missing-1", "last_turns": 5},
            _ERROR,
            description="An unknown session is an error without detail.",
            reader_calls=1,
        ),
        _read_case(
            "codex_threads_list_cwd_prefixes",
            "codex.threads.list",
            {"since_hours": 24, "limit": 50, "cwd_prefixes": ["~/code"]},
            _ok(
                {
                    "threads": [
                        {
                            "thread_id": "cx-friday-1",
                            "cwd": "~/code/friday",
                            "started_at": 1_795_998_600,
                            "last_activity_at": 1_795_998_720,
                            "message_count": 2,
                            "model": "gpt-5-codex",
                            "status_hint": "idle",
                        }
                    ]
                }
            ),
            description="Codex threads from session_meta, filtered by cwd_prefixes.",
            reader_calls=1,
            absent_b64=_absent("cx-acme-1"),
        ),
        _read_case(
            "codex_thread_detail_scrubbed",
            "codex.threads.read",
            {"thread_id": "cx-acme-1", "last_turns": 20},
            _ok({"thread_id": "cx-acme-1", "turns": CODEX_ACME_TURNS}),
            description=(
                "A Codex thread: harness markers, function outputs and reasoning are never "
                "returned; calls are counted on the assistant turn they lead to."
            ),
            reader_calls=1,
            absent_b64=_absent(*FIXTURE_SECRETS, *NEVER_RETURNED),
        ),
        _read_case(
            "usage_cwd_prefixes",
            "usage.ccusage",
            {"period": "week", "cwd_prefixes": ["~/work/acme"]},
            _ok(
                {
                    "total_usd_micros": 63_345,
                    "tokens_in": 4_500,
                    "tokens_out": 700,
                    "by_project": [
                        {
                            "cwd": "~/work/acme",
                            "usd_micros": 63_345,
                            "tokens_in": 4_500,
                            "tokens_out": 700,
                        }
                    ],
                }
            ),
            description=(
                "Spend per project in integer micro-USD (half-even); totals cover the "
                "matching projects only (the nested sibling is not under the prefix)."
            ),
            reader_calls=1,
        ),
        _read_case(
            "git_status",
            "git.status",
            {"repo_path": "~/work/acme"},
            _ok(
                {
                    "branch": "main",
                    "ahead": 0,
                    "behind": 0,
                    "dirty_files_count": 0,
                    "untracked_count": 0,
                    "last_commit_sha": "402c79a89633f13e35867980eb15af1cff398046",
                    "last_commit_at": 1_795_132_800,
                }
            ),
            description="Counts-only git state of a repo on the node-local list.",
            reader_calls=1,
            absent_b64=_absent("README.md"),
        ),
        _read_case(
            "git_status_unlisted_repo",
            "git.status",
            {"repo_path": "~/work/other"},
            _ERROR,
            description="A repo that is not on the node-local list is never read.",
            reader_calls=1,
        ),
        _read_case(
            "windows_titles_off",
            "windows.list",
            {"limit": 30},
            _ok({"windows": WINDOWS}),
            description="Window titles stay on the node unless the owner enabled them locally.",
            reader_calls=1,
            absent_b64=_absent("Invoice", "pytest"),
        ),
        _read_case(
            "windows_titles_on_scrubbed",
            "windows.list",
            {"limit": 2},
            _ok(
                {
                    "windows": [
                        {**WINDOWS[0], "titles": ["acme - pytest", "friday - zsh"]},
                        {**WINDOWS[1], "titles": ["Invoice - [PII:email]"]},
                    ]
                }
            ),
            description="With titles enabled locally, every title is scrubbed; limit applies.",
            setup={"window_titles": True},
            reader_calls=1,
            absent_b64=_absent("owner.fixture@example.com"),
        ),
        _read_case(
            "directory_not_granted",
            "claude.sessions.read",
            {"session_id": "cc-acme-1", "last_turns": 5},
            [refused("not_granted")],
            description="Without the local ~/.claude grant the capability is not_granted.",
            setup={"reader_grants": {"claude": False}},
        ),
    ]


def v1_cases():
    cases = _common_cases(1) + _reader_cases()
    grant_attached = plain("v1-grant", "apps.frontmost", {}, version=1)
    grant_attached = resign({**grant_attached, "grant": {"approval_id": APPROVAL_ID}})
    cases.append(
        case(
            "v1_non_null_grant",
            1,
            check=13,
            description="A v1 node refuses a non-null grant.",
            steps=[send(grant_attached)],
            frames=[refused("class_not_allowed")],
        )
    )
    as_gate = resign({**plain("v1-gate", "apps.frontmost", {}, version=1), "class": "gate"})
    cases.append(
        case(
            "v1_class_not_read",
            1,
            check=13,
            description="A v1 session gets read invokes only.",
            steps=[send(as_gate)],
            frames=[refused("class_not_allowed")],
        )
    )
    return cases


# --- v2: grants, classes, local policy, the window, execution ----------


def _other(name, value):
    if name in ("issued_at", "expires_at", "capability_version"):
        return value + 1
    if name == "args_sha256":
        return "0" * 64
    if name == "nonce":
        return _nonce("other")
    if name == "capability":
        return "ax.press"
    if name == "node_id":
        return OTHER_NODE_ID
    if name == "workspace_id":
        return "2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6e"
    raise KeyError(name)


def _grant_cases():
    cases = []
    base = gated("v2-grant-base")
    grant = base["grant"]

    def with_grant(envelope, new_grant):
        return resign({**envelope, "grant": new_grant})

    def one(name, check, code, frame, description):
        cases.append(
            case(
                name,
                2,
                check=check,
                description=description,
                steps=[send(frame)],
                frames=[refused(code)],
                executor_calls=0,
                windows_shown=0,
            )
        )

    one(
        "v2_gate_without_grant",
        14,
        "grant_missing",
        resign({**base, "grant": None}),
        "A gate invoke without a grant.",
    )
    read = plain("v2-read-grant", "git.status", {"repo_path": "~/work/acme"})
    one(
        "v2_read_with_grant",
        14,
        "grant_unexpected",
        with_grant(read, grant),
        "A read invoke carrying a grant.",
    )
    confirm = plain("v2-confirm-grant", "apps.open", TERMINAL, cls="confirm")
    one(
        "v2_confirm_with_grant",
        14,
        "grant_unexpected",
        with_grant(confirm, grant),
        "A confirm invoke carrying a grant.",
    )
    unpinned = sign_with_domain(_strip(grant), UNPINNED, GRANT_DOMAIN_V2)
    one(
        "v2_grant_unpinned_key",
        15,
        "unknown_key",
        with_grant(base, unpinned),
        "A grant signed by an unpinned key.",
    )
    one(
        "v2_grant_bad_signature",
        16,
        "bad_grant_signature",
        with_grant(base, {**grant, "sig": _flip(grant["sig"])}),
        "A flipped grant signature.",
    )
    one(
        "v2_grant_envelope_domain",
        16,
        "bad_grant_signature",
        with_grant(base, sign_with_domain(_strip(grant), SIGNING, INVOKE_DOMAIN_V2)),
        "A grant signed in the envelope domain.",
    )
    one(
        "v2_grant_no_domain",
        16,
        "bad_grant_signature",
        with_grant(base, sign_with_domain(_strip(grant), SIGNING, ENVELOPE_DOMAINS[1])),
        "A grant signed without a domain (v1 style).",
    )
    for name in SHARED_FIELDS:
        changed = {**base, name: _other(name, base[name])}
        code, check = "grant_mismatch", 17
        if name == "capability":
            changed["args"] = {"bundle_id": "com.apple.Terminal", "role": "button", "title": "OK"}
            changed["args_sha256"] = grant["args_sha256"]
        if name == "node_id":
            code, check = "wrong_node", 7
        if name == "capability_version":
            code, check = "unknown_capability", 12
        one(
            f"v2_envelope_{name}_differs_from_grant",
            check,
            code,
            resign(changed),
            f"The envelope's {name} differs from the grant's.",
        )
        rebound = sign_with_domain(
            {**_strip(grant), name: _other(name, grant[name])}, SIGNING, GRANT_DOMAIN_V2
        )
        one(
            f"v2_grant_{name}_differs_from_envelope",
            17,
            "grant_mismatch",
            with_grant(base, rebound),
            f"The grant's {name} differs from the envelope's.",
        )
    one(
        "v2_gate_ttl_121",
        10,
        "ttl_too_long",
        resign({**base, "expires_at": base["issued_at"] + 121}),
        "A gated TTL of 121 s.",
    )
    one(
        "v2_grant_expired",
        9,
        "expired",
        gated("v2-expired", issued=BASE - 200),
        "An expired grant (and envelope).",
    )
    gate_as_confirm = gated("v2-downgrade", "apps.open", TERMINAL)
    one(
        "v2_class_gate_for_confirm_entry",
        13,
        "class_mismatch",
        gate_as_confirm,
        "Class upgrade: a gate envelope for a confirm entry.",
    )
    one(
        "v2_class_confirm_for_gate_entry",
        13,
        "class_mismatch",
        resign({**base, "class": "confirm", "grant": None}),
        "Class downgrade: a confirm envelope for a gate entry.",
    )
    one(
        "v2_args_outside_node_schema_recipe",
        20,
        "args_schema",
        gated(
            "v2-recipe-level",
            "applescript.recipe.run",
            {"recipe": "system.volume_set", "params": {"level": 101}},
        ),
        "Recipe params outside the node's own recipe schema.",
    )
    cases.append(
        case(
            "v2_args_outside_node_schema",
            2,
            check=20,
            description="Args valid for the server contract but outside the node's own schema.",
            setup={"catalogue": "narrow"},
            steps=[
                send(gated("v2-narrow", args={"repo_path": "~/work/acme", "command_kind": "build"}))
            ],
            frames=[refused("args_schema")],
            executor_calls=0,
        )
    )
    cases.append(
        case(
            "v2_gate_not_granted",
            2,
            check=21,
            description="A gate capability present but not granted (no TCC grant).",
            setup={"availability": {"repo.command.run": "not_granted"}},
            steps=[send(gated("v2-gate-not-granted"))],
            frames=[refused("not_granted")],
            executor_calls=0,
        )
    )
    deny = [{"action": "deny", "after": 0}]
    cases.append(
        case(
            "v2_replay_same_grant_twice",
            2,
            check=11,
            description="The same gated envelope twice: the second is a replay.",
            setup={"window": deny},
            steps=[send(base), send(base)],
            frames=[
                confirmation("shown"),
                confirmation("denied"),
                refused("owner_denied"),
                refused("replay"),
            ],
            executor_calls=0,
        )
    )
    reused = resign(
        {**base, "id": _uuid4("reused-msg"), "invocation_id": _uuid4("reused-invocation")}
    )
    cases.append(
        case(
            "v2_replay_grant_on_new_envelope",
            2,
            check=11,
            description="The same grant on a new envelope (same nonce): a replay.",
            setup={"window": deny},
            steps=[send(base), send(reused)],
            frames=[
                confirmation("shown"),
                confirmation("denied"),
                refused("owner_denied"),
                refused("replay"),
            ],
            executor_calls=0,
        )
    )
    new_nonce = resign({**reused, "nonce": _nonce("fresh")})
    cases.append(
        case(
            "v2_replay_grant_on_new_envelope_new_nonce",
            2,
            check=17,
            description="The same grant on a new envelope with a fresh nonce: grant_mismatch.",
            setup={"window": deny},
            steps=[send(base), send(new_nonce)],
            frames=[
                confirmation("shown"),
                confirmation("denied"),
                refused("owner_denied"),
                refused("grant_mismatch"),
            ],
            executor_calls=0,
        )
    )
    return cases


def _policy_cases():
    cases = []

    def nc(name, frame, description):
        cases.append(
            case(
                name,
                2,
                check=22,
                description=description,
                steps=[send(frame)],
                frames=[refused("not_configured")],
                executor_calls=0,
                windows_shown=0,
            )
        )

    nc(
        "v2_not_configured_unmapped_repo",
        gated("v2-nc-repo", args={"repo_path": "~/work/other", "command_kind": "test"}),
        "A repo the node has no mapping for.",
    )
    nc(
        "v2_not_configured_unmapped_kind",
        gated("v2-nc-kind", args={"repo_path": "~/work/acme", "command_kind": "typecheck"}),
        "A command kind not mapped for the repo.",
    )
    nc(
        "v2_not_configured_disabled_recipe",
        gated(
            "v2-nc-recipe", "applescript.recipe.run", {"recipe": "music.next_track", "params": {}}
        ),
        "A recipe not enabled locally.",
    )
    nc(
        "v2_not_configured_bundle_id",
        plain("v2-nc-bundle", "apps.open", {"bundle_id": "com.example.Other"}, cls="confirm"),
        "A bundle id not on the local list.",
    )
    nc(
        "v2_not_configured_symlink",
        gated("v2-nc-link", args={"repo_path": "~/work/link", "command_kind": "test"}),
        "A repo_path symlink to an unmapped directory.",
    )
    nc(
        "v2_not_configured_repo_recipe",
        gated(
            "v2-nc-reveal",
            "applescript.recipe.run",
            {"recipe": "finder.reveal_repo", "params": {"repo_path": "~/work/other"}},
        ),
        "A repo recipe for an unmapped repo.",
    )
    cases.append(
        case(
            "v2_symlink_to_mapped_root_resolves",
            2,
            check=23,
            description="A symlink that resolves to a mapped root reaches the window.",
            setup={"window": [{"action": "deny", "after": 0}]},
            steps=[
                send(
                    gated(
                        "v2-link-ok", args={"repo_path": "~/work/acme-link", "command_kind": "test"}
                    )
                )
            ],
            frames=[confirmation("shown"), confirmation("denied"), refused("owner_denied")],
            executor_calls=0,
        )
    )
    return cases


def _window_cases():
    cases = []
    approve = [{"action": "approve", "after": 2}]
    cases.append(
        case(
            "v2_gate_command_runs_after_click",
            2,
            check=None,
            description="Approved on the Mac: the mapped command runs, output streams, result.",
            setup={
                "window": approve,
                "executor": [{"chunks": [["stdout", "acme tests ok\n", 0]], "exit_code": 0}],
            },
            steps=[send(gated("v2-run")), advance(2)],
            frames=[
                confirmation("shown"),
                confirmation("approved"),
                {"type": "output", "seq": 0, "stream": "stdout", "data": "acme tests ok\n"},
                {
                    "type": "result",
                    "status": "ok",
                    "exit_code": 0,
                    "confirm_state": "approved",
                    "tail": "acme tests ok\n",
                    "output_bytes": 14,
                    "sandbox_violation": False,
                },
            ],
            executor_calls=1,
            windows_shown=1,
        )
    )
    cases.append(
        case(
            "v2_gate_command_worktree_mode",
            2,
            description="worktree mode: a throwaway worktree, deleted afterwards.",
            setup={
                "window": [{"action": "approve", "after": 0}],
                "executor": [{"chunks": [["stdout", "lint ok\n", 0]], "exit_code": 0}],
            },
            steps=[send(gated("v2-wt", args=ACME_LINT))],
            frames=[
                confirmation("shown"),
                confirmation("approved"),
                {"type": "output", "data": "lint ok\n"},
                {"type": "result", "status": "ok", "exit_code": 0},
            ],
            executor_calls=1,
            worktrees_after=1,
        )
    )
    secret = SECRET_PREFIX + SECRET_TAIL
    cases.append(
        case(
            "v2_output_secret_split_across_chunks",
            2,
            description="A secret split across two chunks is scrubbed on the node.",
            setup={
                "window": [{"action": "approve", "after": 0}],
                "executor": [
                    {
                        "chunks": [
                            ["stdout", b64(("export OPENAI_API_KEY=" + secret[:9]).encode()), 0],
                            ["stdout", b64((secret[9:] + "\ndone\n").encode()), 1],
                        ],
                        "duration": 1,
                        "exit_code": 0,
                    }
                ],
            },
            steps=[send(gated("v2-secret")), advance(1)],
            frames=[
                confirmation("shown"),
                confirmation("approved"),
                {"type": "output", "stream": "stdout"},
                {"type": "result", "status": "ok", "exit_code": 0},
            ],
            absent_b64=[
                b64(n.encode())["b64"] for n in (secret, SECRET_TAIL[:20], SECRET_TAIL[-20:])
            ],
        )
    )
    cases.append(
        case(
            "v2_sandbox_violation_reported",
            2,
            description="A command the sandbox stopped reports sandbox_violation.",
            setup={
                "window": [{"action": "approve", "after": 0}],
                "executor": [
                    {
                        "chunks": [["stderr", "denied\n", 0]],
                        "exit_code": 1,
                        "sandbox_violation": True,
                    }
                ],
            },
            steps=[send(gated("v2-violation"))],
            frames=[
                confirmation("shown"),
                confirmation("approved"),
                {"type": "output", "stream": "stderr"},
                {"type": "result", "status": "ok", "exit_code": 1, "sandbox_violation": True},
            ],
        )
    )
    cases.append(
        case(
            "v2_confirm_apps_open_after_click",
            2,
            description="A confirm-class open runs only after the node click.",
            setup={"window": [{"action": "approve", "after": 1}]},
            steps=[send(plain("v2-open", "apps.open", TERMINAL, cls="confirm")), advance(1)],
            frames=[
                confirmation("shown"),
                confirmation("approved"),
                {"type": "result", "status": "ok", "exit_code": None, "confirm_state": "approved"},
            ],
            executor_calls=1,
        )
    )
    cases.append(
        case(
            "v2_gate_recipe_after_click",
            2,
            description="An enabled recipe runs after the click.",
            setup={"window": [{"action": "approve", "after": 0}]},
            steps=[
                send(
                    gated(
                        "v2-recipe",
                        "applescript.recipe.run",
                        {"recipe": "music.play_pause", "params": {}},
                    )
                )
            ],
            frames=[
                confirmation("shown"),
                confirmation("approved"),
                {"type": "result", "status": "ok", "confirm_state": "approved"},
            ],
            executor_calls=1,
        )
    )
    cases.append(
        case(
            "v2_owner_denies",
            2,
            check=23,
            description="The owner clicks Reddet.",
            setup={"window": [{"action": "deny", "after": 3}]},
            steps=[send(gated("v2-deny")), advance(3)],
            frames=[
                confirmation("shown"),
                confirmation("denied"),
                {**refused("owner_denied"), "confirm_state": "denied"},
            ],
            executor_calls=0,
        )
    )
    cases.append(
        case(
            "v2_owner_times_out",
            2,
            check=23,
            description="No click within 60 s: confirm_timeout (nothing at 59 s).",
            steps=[send(gated("v2-timeout")), advance(59), advance(1)],
            frames=[
                confirmation("shown"),
                confirmation("timed_out"),
                {**refused("confirm_timeout"), "confirm_state": "timed_out"},
            ],
            executor_calls=0,
        )
    )
    cases.append(
        case(
            "v2_busy_confirming",
            2,
            check=23,
            description="A second gated invoke while a window is open.",
            steps=[send(gated("v2-busy-a")), send(gated("v2-busy-b"))],
            frames=[confirmation("shown"), refused("busy_confirming")],
            executor_calls=0,
            windows_shown=1,
        )
    )
    cases.append(
        case(
            "v2_expired_after_confirm",
            2,
            check=24,
            description="The grant expires while the window is open (clicked at 95 s).",
            setup={"now": BASE + 40, "window": [{"action": "approve", "after": 55}]},
            steps=[send(gated("v2-late")), advance(55)],
            frames=[
                confirmation("shown"),
                confirmation("approved"),
                {**refused("expired_after_confirm"), "confirm_state": "approved"},
            ],
            executor_calls=0,
        )
    )
    cases.append(
        case(
            "v2_confirm_expired_after_confirm",
            2,
            check=24,
            description="A confirm envelope expiring inside the window is refused after the click.",
            setup={"now": BASE + 40, "window": [{"action": "approve", "after": 55}]},
            steps=[
                send(plain("v2-confirm-late", "apps.open", TERMINAL, cls="confirm")),
                advance(55),
            ],
            frames=[
                confirmation("shown"),
                confirmation("approved"),
                {**refused("expired_after_confirm"), "confirm_state": "approved"},
            ],
            executor_calls=0,
        )
    )
    cases.append(
        case(
            "v2_kill_switch_closes_window",
            2,
            description="The local kill switch closes an open window; nothing runs.",
            steps=[send(gated("v2-kill")), KILL_SWITCH],
            frames=[
                confirmation("shown"),
                confirmation("closed"),
                {**refused("owner_denied"), "confirm_state": "closed"},
            ],
            executor_calls=0,
        )
    )
    cases.append(
        case(
            "v2_socket_dropped_window_open",
            2,
            description="The socket drops while the window is open: nothing runs, no result.",
            setup={"window": [{"action": "approve", "after": 5}]},
            steps=[send(gated("v2-drop-window")), DISCONNECT, advance(10)],
            frames=[confirmation("shown")],
            executor_calls=0,
            closed_reason="socket_lost",
        )
    )
    cases.append(
        case(
            "v2_socket_dropped_while_running",
            2,
            description="The socket drops while a command runs: it is killed, no late result.",
            setup={
                "window": [{"action": "approve", "after": 0}],
                "executor": [{"chunks": [["stdout", "started\n", 0]], "hang": True}],
            },
            steps=[send(gated("v2-drop-run")), DISCONNECT, advance(10)],
            frames=[
                confirmation("shown"),
                confirmation("approved"),
                {"type": "output", "data": "started\n"},
            ],
            executor_calls=1,
            executor_terminated=1,
        )
    )
    cases.append(
        case(
            "v2_runtime_cap",
            2,
            description="A command past its runtime cap is stopped (timeout).",
            setup={
                "window": [{"action": "approve", "after": 0}],
                "executor": [{"chunks": [], "hang": True}],
            },
            steps=[send(gated("v2-cap")), advance(299), advance(1)],
            frames=[
                confirmation("shown"),
                confirmation("approved"),
                {"type": "result", "status": "timeout", "exit_code": None},
            ],
            executor_calls=1,
            executor_terminated=1,
        )
    )
    return cases


def v2_cases():
    return _common_cases(2) + _grant_cases() + _policy_cases() + _window_cases()


@cache
def all_cases():
    """`{version: [case, ...]}`; names are unique across versions."""
    cases = {1: v1_cases(), 2: v2_cases()}
    names = [c["name"] for version in cases.values() for c in version]
    if len(names) != len(set(names)):
        raise ValueError("duplicate conformance case name")
    return cases


def render_case(document) -> str:
    return json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True) + "\n"


def b64(data: bytes) -> dict:
    return {"b64": base64.b64encode(data).decode("ascii")}
