"""`friday-node-linux` (Phase 13 brief D10).

    friday-node-linux pair --server https://friday-home.local --code <code> \\
        (--ca ca.pem | --tls-pin-sha256 <spki>) [--name Masaüstü]
    friday-node-linux run                      # the daemon (systemd user unit)
    friday-node-linux hook                     # Claude Code command hook -> the node
    friday-node-linux grant claude|codex       # local policy, interactive TTY only
    friday-node-linux revoke claude|codex
    friday-node-linux repo add <path> [--name N] | repo remove <name> | repo list
    friday-node-linux policy show
    friday-node-linux config set server-url https://<new host>   # re-point, keeps pins
    friday-node-linux status
    friday-node-linux rotate                   # rotate the node secret now
    friday-node-linux unpair                   # forget the local credential
    friday-node-linux conformance --hook-socket <sock> --gateway <host:port> --ca <cert>

The policy commands are the only way the local policy changes: each needs
an interactive terminal (stdin and stdout), prints the exact change and
asks the local user to type the directory's basename; a wrong word or no
TTY changes nothing. Nothing here ever prints the node secret or the
pairing code.
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from friday_node_linux import paths
from friday_node_linux._vendor.nodes.reference.hook_relay import MAX_INPUT_BYTES, relay_stdin
from friday_node_linux.api import PAIR_PATH, USER_AGENT, RotationError, post_json, rotate
from friday_node_linux.credential import (
    Credential,
    CredentialError,
    check_server_url,
    load_credential,
    save_credential,
)
from friday_node_linux.daemon import Daemon, signal_daemon
from friday_node_linux.policy import (
    GRANT_DIRS,
    GRANT_LABELS,
    LinuxPolicy,
    PolicyError,
    display_root,
)
from friday_node_linux.tls import PinError, Pins, check_ca_pem, parse_spki_pin

DEFAULT_NODE_NAME = ""  # the server names a blank node "Linux düğümü"


class Io:
    """The terminal (injectable for tests)."""

    def __init__(self, stdin=None, stdout=None, stderr=None, is_tty=None, env=None, home=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self._is_tty = is_tty
        self.env = os.environ if env is None else env
        self.home = Path(home) if home is not None else Path.home()

    def is_tty(self) -> bool:
        if self._is_tty is not None:
            return self._is_tty()
        try:
            return os.isatty(self.stdin.fileno()) and os.isatty(self.stdout.fileno())
        except (AttributeError, OSError, ValueError):
            return False

    def out(self, text=""):
        print(text, file=self.stdout, flush=True)

    def err(self, text):
        print(text, file=self.stderr, flush=True)


# --- the local policy (interactive only) -------------------------------------


def confirm(io: Io, change: str, word: str) -> bool:
    if not io.is_tty():
        io.err("refused: local policy changes need an interactive terminal (no change made)")
        return False
    io.out(change)
    io.out(f"Type {word!r} to confirm, anything else cancels:")
    answer = io.stdin.readline()
    if answer.rstrip("\n") != word:
        io.err("not confirmed (no change made)")
        return False
    return True


def _apply(io: Io, policy: LinuxPolicy) -> int:
    policy.save(paths.policy_path(io.env))
    if signal_daemon(paths.pid_path(io.env)):
        io.out("saved; the running node reloads its policy (its next session uses it)")
    else:
        io.out("saved; the node uses it the next time it starts")
    return 0


def _load_policy(io: Io):
    try:
        return LinuxPolicy.load(paths.policy_path(io.env))
    except PolicyError as exc:
        io.err(f"refusing the local policy: {exc}")
        return None


def cmd_grant(args, io: Io, *, granted: bool) -> int:
    policy = _load_policy(io)
    if policy is None:
        return 1
    family = args.family
    directory = io.home / GRANT_DIRS[family]
    if policy.grants[family] == granted:
        io.out(f"{family} is already {'granted' if granted else 'not granted'}; nothing to do")
        return 0
    verb = "GRANT" if granted else "REVOKE"
    change = (
        f"{verb} read access to {directory}\n"
        f"  ({GRANT_LABELS[family]}; bounded and scrubbed, never tool results or file contents)"
    )
    if not confirm(io, change, directory.name):
        return 1
    return _apply(io, policy.with_grant(family, granted))


def cmd_repo_add(args, io: Io) -> int:
    policy = _load_policy(io)
    if policy is None:
        return 1
    path = Path(os.path.expanduser(args.path))
    if not path.is_absolute():
        path = Path.cwd() / path
    real = Path(os.path.realpath(path))
    if not real.is_dir() or not (real / ".git").exists():
        io.err(f"refused: {real} is not a git repository root")
        return 1
    name = args.name or real.name
    root = display_root(real, io.home)
    try:
        updated = policy.with_repo(name, root)
    except PolicyError as exc:
        io.err(f"refused: {exc}")
        return 1
    change = (
        f"REGISTER repo {name!r} at {root}\n"
        "  (git status counts only: branch, ahead/behind, dirty files)"
    )
    if not confirm(io, change, real.name):
        return 1
    return _apply(io, updated)


def cmd_repo_remove(args, io: Io) -> int:
    policy = _load_policy(io)
    if policy is None:
        return 1
    repo = next((r for r in policy.repos if r.name == args.name), None)
    if repo is None:
        io.err(f"refused: no repo named {args.name!r}")
        return 1
    change = f"UNREGISTER repo {repo.name!r} at {repo.root}"
    if not confirm(io, change, os.path.basename(repo.root.rstrip("/")) or repo.name):
        return 1
    return _apply(io, policy.without_repo(repo.name))


def cmd_repo_list(args, io: Io) -> int:
    policy = _load_policy(io)
    if policy is None:
        return 1
    for repo in policy.repos:
        io.out(f"{repo.name}\t{repo.root}")
    return 0


def cmd_policy_show(args, io: Io) -> int:
    policy = _load_policy(io)
    if policy is None:
        return 1
    for family, directory in GRANT_DIRS.items():
        state = "granted" if policy.grants[family] else "not granted"
        io.out(f"{family}: {state} (~/{directory})")
    io.out(f"repos: {len(policy.repos)}")
    for repo in policy.repos:
        io.out(f"  {repo.name}\t{repo.root}")
    io.out(f"fingerprint: {policy.fingerprint()}")
    return 0


# --- pairing and the credential --------------------------------------------------


def _pins(args) -> Pins:
    ca = spki = None
    if args.ca:
        ca = check_ca_pem(Path(args.ca).read_text(encoding="ascii"))
    if args.tls_pin_sha256:
        spki = parse_spki_pin(args.tls_pin_sha256)
    return Pins(ca_pem=ca, spki_sha256=spki)


def cmd_pair(args, io: Io, *, now=None) -> int:
    target = paths.credential_path(io.env)
    if os.path.lexists(target):
        io.err("already paired: run `friday-node-linux unpair` first (revoke it on /devices too)")
        return 1
    try:
        server_url = check_server_url(args.server)
        pins = _pins(args)
    except (OSError, ValueError) as exc:
        io.err(f"refused: {exc}")
        return 1
    body = {
        "code": args.code,
        "node_name": args.name,
        "protocol_versions": [1],
        "platform": "linux",
    }
    try:
        status, data = post_json(server_url, PAIR_PATH, body, pins=pins)
    except PinError as exc:
        io.err(f"refused: {exc}")
        return 1
    except OSError as exc:
        io.err(f"pairing failed: cannot reach the server ({exc.__class__.__name__})")
        return 1
    if status != 201:
        code = data.get("code") if isinstance(data, dict) else None
        io.err(f"pairing refused by the server: HTTP {status} {code or ''}".rstrip())
        return 1
    try:
        credential = Credential.from_pairing(
            data, server_url=server_url, pins=pins, now=now or time.time()
        )
    except CredentialError as exc:
        io.err(f"pairing failed: {exc}")
        return 1
    paths.ensure_private_dir(paths.state_dir(io.env))
    save_credential(target, credential)
    keys = ", ".join(key_id for key_id, _ in credential.signing_keys)
    io.out(f"paired: node {credential.node_id} on {credential.host} (signing key {keys})")
    io.out("next: friday-node-linux grant claude (and/or codex), then start the user unit")
    return 0


def cmd_unpair(args, io: Io) -> int:
    target = paths.credential_path(io.env)
    if not os.path.lexists(target):
        io.out("not paired; nothing to do")
        return 0
    os.unlink(target)
    io.out("local credential removed; revoke the node on /devices too")
    return 0


def cmd_rotate(args, io: Io) -> int:
    """Rotate the node secret now (the daemon also does it when due)."""
    target = paths.credential_path(io.env)
    try:
        credential = load_credential(target)
        rotated = rotate(credential)
    except CredentialError as exc:
        io.err(f"refused: {exc}")
        return 1
    except (RotationError, PinError) as exc:
        io.err(f"rotation failed: {exc}")
        return 1
    save_credential(target, rotated)
    io.out(f"rotated: node {rotated.node_id}; restart the node to use the new secret")
    return 0


def cmd_set_server_url(args, io: Io) -> int:
    """The VPS re-point (ADR-017 decision 7): the new server must present a
    certificate that passes the pins taken at pairing; every pin is kept."""
    try:
        credential = load_credential(paths.credential_path(io.env))
        updated = credential.with_server_url(args.url)
    except (CredentialError, ValueError) as exc:
        io.err(f"refused: {exc}")
        return 1
    try:
        sock = credential.pins.connect(updated.host, updated.port)
        sock.close()
    except PinError as exc:
        io.err(f"refused: the new server does not match this node's pins ({exc}); no change made")
        return 1
    except OSError as exc:
        io.err(f"refused: cannot reach the new server ({exc.__class__.__name__}); no change made")
        return 1
    save_credential(paths.credential_path(io.env), updated)
    io.out(f"server is now {updated.host}:{updated.port} (pins kept); restart the node")
    return 0


def cmd_status(args, io: Io) -> int:
    try:
        credential = load_credential(paths.credential_path(io.env))
    except CredentialError as exc:
        io.out(f"credential: {exc}")
        credential = None
    if credential is not None:
        io.out(f"node: {credential.node_id}")
        io.out(f"server: {credential.server_url}{credential.gateway_path}")
        io.out(f"signing keys: {', '.join(k for k, _ in credential.signing_keys)}")
        pins = credential.pins
        kinds = [name for name, value in (("ca", pins.ca_pem), ("spki", pins.spki_sha256)) if value]
        io.out(f"tls pins: {', '.join(kinds)}")
    return cmd_policy_show(args, io)


# --- the daemon, the hook, conformance ------------------------------------------------


def cmd_run(args, io: Io) -> int:
    logging.basicConfig(stream=io.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        paths.ensure_private_dir(paths.state_dir(io.env))
        credential = load_credential(paths.credential_path(io.env))
    except (CredentialError, paths.UnsafePath) as exc:
        io.err(f"refusing to start: {exc}")
        return 1
    daemon = Daemon(
        credential=credential,
        credential_path=paths.credential_path(io.env),
        policy_path=paths.policy_path(io.env),
        home=io.home,
        spool_dir=paths.spool_dir(io.env),
        hook_socket=paths.hook_socket_path(io.env),
        pid_path=paths.pid_path(io.env),
    )
    daemon.install_signal_handlers()
    return daemon.run()


def cmd_hook(args, io: Io) -> int:
    """Claude Code's command hook: always exits 0 within 3 s, prints nothing."""
    try:
        data = io.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    except Exception:  # noqa: BLE001 - never fail Claude Code
        return 0
    return relay_stdin(paths.hook_socket_path(io.env), data)


def cmd_conformance(args, io: Io) -> int:
    from friday_node_linux.conformance import run_conformance

    try:
        pins = _pins(args)
    except (OSError, ValueError) as exc:
        io.err(f"refused: {exc}")
        return 1
    return run_conformance(hook_socket=args.hook_socket, gateway=args.gateway, pins=pins)


# --- argument parsing -------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="friday-node-linux", description=__doc__.split("\n")[0])
    root.add_argument("--version", action="version", version=USER_AGENT)
    sub = root.add_subparsers(dest="command", required=True)

    pair = sub.add_parser("pair", help="pair with the Friday server (once)")
    pair.add_argument("--server", required=True)
    pair.add_argument("--code", required=True)
    pair.add_argument("--name", default=DEFAULT_NODE_NAME)
    pair.add_argument("--ca", default=None, help="the private CA (or server certificate) PEM")
    pair.add_argument("--tls-pin-sha256", default=None, help="the server certificate's SPKI sha256")

    sub.add_parser("run", help="run the node (the systemd user unit)")
    sub.add_parser("hook", help="relay a Claude Code hook event from stdin")
    sub.add_parser("unpair", help="forget the local credential")
    sub.add_parser("rotate", help="rotate the node secret now")
    sub.add_parser("status", help="show the pairing and the local policy")

    for name in ("grant", "revoke"):
        grant = sub.add_parser(name, help=f"{name} a directory read grant (interactive)")
        grant.add_argument("family", choices=sorted(GRANT_DIRS))

    repo = sub.add_parser("repo", help="registered repos for git.status (interactive)")
    repo_sub = repo.add_subparsers(dest="repo_command", required=True)
    add = repo_sub.add_parser("add")
    add.add_argument("path")
    add.add_argument("--name", default=None)
    remove = repo_sub.add_parser("remove")
    remove.add_argument("name")
    repo_sub.add_parser("list")

    policy = sub.add_parser("policy", help="show the local policy")
    policy.add_argument("action", choices=("show",))

    config = sub.add_parser("config", help="change the server URL (re-point)")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_set = config_sub.add_parser("set")
    config_set.add_argument("key", choices=("server-url",))
    config_set.add_argument("url")

    conformance = sub.add_parser("conformance", help="serve the external conformance hook")
    conformance.add_argument("--hook-socket", required=True)
    conformance.add_argument("--gateway", required=True, help="host:port of the fake gateway")
    conformance.add_argument("--ca", default=None)
    conformance.add_argument("--tls-pin-sha256", default=None)
    return root


def main(argv=None, io: Io | None = None) -> int:
    io = io or Io()
    args = parser().parse_args(sys.argv[1:] if argv is None else argv)
    command = args.command
    if command == "pair":
        return cmd_pair(args, io)
    if command == "run":
        return cmd_run(args, io)
    if command == "hook":
        return cmd_hook(args, io)
    if command == "rotate":
        return cmd_rotate(args, io)
    if command == "unpair":
        return cmd_unpair(args, io)
    if command == "status":
        return cmd_status(args, io)
    if command in ("grant", "revoke"):
        return cmd_grant(args, io, granted=command == "grant")
    if command == "repo":
        handler = {"add": cmd_repo_add, "remove": cmd_repo_remove, "list": cmd_repo_list}
        return handler[args.repo_command](args, io)
    if command == "policy":
        return cmd_policy_show(args, io)
    if command == "config":
        return cmd_set_server_url(args, io)
    if command == "conformance":
        return cmd_conformance(args, io)
    return 2  # pragma: no cover - argparse refuses unknown commands
