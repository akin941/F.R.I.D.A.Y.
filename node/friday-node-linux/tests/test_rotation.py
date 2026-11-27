"""Node secret rotation (`POST /api/v1/auth/nodes/credential/rotate`,
Phase 9 D1) by the daemon when due and by `friday-node-linux rotate`: the
current secret as Bearer over pinned TLS, the new one saved 0600 before it
is used, never printed or logged."""

import logging
import os
import stat

import pytest

from friday_node_linux import api, paths
from friday_node_linux.cli import main
from friday_node_linux.credential import CredentialError, load_credential, save_credential
from friday_node_linux.daemon import ROTATION_RETRY_SECONDS, Daemon

from .conftest import SECRET
from .test_credential import NODE_ID, FakeFriday, make_credential

NEW_SECRET = "jnd_" + "N3wN0deSecretValue_" * 3
DAY = 86400


def rotation_answer(**overrides):
    body = {
        "node_id": NODE_ID,
        "node_secret": NEW_SECRET,
        "secret_issued_at": "2026-11-26T00:00:00Z",
        "previous_valid_until": "2026-11-26T00:10:00Z",
        "credential_expires_at": "2027-02-24T00:00:00Z",
        "rotate_after_days": 30,
    }
    body.update(overrides)
    return body


@pytest.fixture
def server(tls):
    made = []

    def make(status=200, body=None, cert=None):
        fake = FakeFriday(
            cert or tls,
            routes={api.ROTATE_PATH: (status, body if body is not None else rotation_answer())},
        )
        made.append(fake)
        return fake

    yield make
    for fake in made:
        fake.close()


def test_rotate_sends_the_current_secret_and_returns_the_new_one(server, tls):
    fake = server()
    credential = make_credential(tls, url=fake.url)
    rotated = api.rotate(credential, now=credential.paired_at + 31 * DAY)
    assert [path for path, _ in fake.requests] == [api.ROTATE_PATH]
    assert fake.authorization == [f"Bearer {SECRET}"]
    assert rotated.node_secret == NEW_SECRET
    assert rotated.secret_issued_at == credential.paired_at + 31 * DAY
    assert rotated.pins == credential.pins and rotated.signing_keys == credential.signing_keys
    assert rotated.paired_at == credential.paired_at


def test_a_refused_rotation_raises_with_the_code_only(server, tls):
    fake = server(status=403, body={"code": "permission_denied", "detail": "x"})
    with pytest.raises(api.RotationError) as exc:
        api.rotate(make_credential(tls, url=fake.url))
    assert exc.value.status == 403 and exc.value.code == "permission_denied"
    assert SECRET not in str(exc.value)


def test_a_rotation_for_another_node_is_refused(server, tls):
    fake = server(body=rotation_answer(node_id="00000000-0000-4000-8000-000000000000"))
    with pytest.raises(CredentialError):
        api.rotate(make_credential(tls, url=fake.url))


def test_rotation_never_reaches_a_server_off_the_pins(server, tls, other_tls):
    fake = server(cert=other_tls)
    with pytest.raises(api.RotationError):
        api.rotate(make_credential(tls, url=fake.url))
    assert fake.requests == []  # the secret never left the node


def test_the_cli_rotates_and_saves_0600(server, tls, make_io, env):
    fake = server()
    save_credential(paths.credential_path(env), make_credential(tls, url=fake.url))
    term = make_io()
    assert main(["rotate"], term.io) == 0
    stored = load_credential(paths.credential_path(env))
    assert stored.node_secret == NEW_SECRET
    assert stat.S_IMODE(os.stat(paths.credential_path(env)).st_mode) == 0o600
    printed = term.stdout.getvalue() + term.stderr.getvalue()
    assert NEW_SECRET not in printed and SECRET not in printed


class Clock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return int(self.value)


def daemon_for(tmp_path, home, credential, clock, rotate):
    path = tmp_path / "state" / "credential"
    save_credential(path, credential)
    return (
        Daemon(
            credential=credential,
            credential_path=path,
            policy_path=tmp_path / "policy.json",
            home=home,
            spool_dir=tmp_path / "state" / "spool",
            hook_socket=tmp_path / "run" / "hook.sock",
            clock=clock,
            rotate=rotate,
        ),
        path,
    )


def test_the_daemon_rotates_only_when_due(tmp_path, home, tls, caplog):
    caplog.set_level(logging.DEBUG)
    credential = make_credential(tls)
    calls = []

    def rotate(current, *, now):
        calls.append(now)
        return current.with_secret(NEW_SECRET, now=now)

    clock = Clock(credential.secret_issued_at + 29 * DAY)
    daemon, path = daemon_for(tmp_path, home, credential, clock, rotate)
    daemon.maybe_rotate()
    assert calls == [] and daemon.credential.node_secret == SECRET
    clock.value = credential.secret_issued_at + 30 * DAY
    daemon.maybe_rotate()
    assert len(calls) == 1
    assert daemon.credential.node_secret == NEW_SECRET
    assert load_credential(path).node_secret == NEW_SECRET  # saved before use
    daemon.maybe_rotate()  # not due again for 30 days
    assert len(calls) == 1
    assert NEW_SECRET not in caplog.text and SECRET not in caplog.text


def test_a_failed_rotation_keeps_the_secret_and_waits_an_hour(tmp_path, home, tls):
    credential = make_credential(tls)
    calls = []

    def rotate(current, *, now):
        calls.append(now)
        raise api.RotationError(503, None)

    clock = Clock(credential.secret_issued_at + 40 * DAY)
    daemon, path = daemon_for(tmp_path, home, credential, clock, rotate)
    before = path.read_bytes()
    daemon.maybe_rotate()
    daemon.maybe_rotate()
    assert len(calls) == 1
    assert daemon.credential == credential and path.read_bytes() == before
    clock.value += ROTATION_RETRY_SECONDS
    daemon.maybe_rotate()
    assert len(calls) == 2
