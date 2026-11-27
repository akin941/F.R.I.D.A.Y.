"""Phase 13b branch 2 (brief D2, ADR-017 amendment 2): `home_backup_fresh`
checks the off-site DESTINATION the last backup recorded, not the bucket.

`HOME_BACKUP_OFFSITE_REPOSITORY` unset -> the Phase 13 age rules; set and
equal to `status.json`'s `repository` -> pass, naming R2 for a Cloudflare
R2 host; `repository` absent (an old script) -> warn; different -> fail
naming only scheme and host. A configured or recorded value with userinfo
(`@`) or a query (`?`) fails WITHOUT echoing it, and no detail ever holds a
key. No call to R2: the web container holds no bucket credential."""

import pytest

from ops import preflight
from ops.preflight import FAIL, PASS, WARN
from ops.tests.test_preflight_home import write_status

R2 = "s3:https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com/friday-backup"
OTHER = "s3:https://s3.eu-central-1.amazonaws.com/friday-backup"
SECRET = "hunter2-secret-access-key"


@pytest.fixture
def status_dir(settings, tmp_path):
    settings.HOME_BACKUP_STATUS_PATH = str(tmp_path / "status.json")
    settings.HOME_BACKUP_WARN_HOURS = 26
    settings.HOME_BACKUP_FAIL_HOURS = 72
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = ""
    return tmp_path


def _row():
    return preflight._check_home_backup_fresh()


def test_unset_keeps_the_phase_13_rules(status_dir):
    write_status(status_dir, hours_ago=3)
    row = _row()
    assert row.status == PASS
    assert "off-site" not in row.detail
    # A recorded repository is accepted (and not required) when unset.
    write_status(status_dir, hours_ago=3, repository=R2)
    assert _row().status == PASS
    write_status(status_dir, hours_ago=30, repository=R2)
    assert _row().status == WARN
    write_status(status_dir, hours_ago=80, repository=R2)
    assert _row().status == FAIL


def test_set_and_equal_passes_naming_r2(status_dir, settings):
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = R2
    write_status(status_dir, hours_ago=3, repository=R2)
    row = _row()
    assert row.status == PASS
    assert row.detail.endswith(", off-site R2")
    # A trailing slash on either side is not a different repository.
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = R2 + "/"
    assert _row().status == PASS


def test_set_and_equal_for_another_repository_names_its_host(status_dir, settings):
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = OTHER
    write_status(status_dir, hours_ago=3, repository=OTHER)
    row = _row()
    assert row.status == PASS
    assert "off-site s3:https://s3.eu-central-1.amazonaws.com" in row.detail
    assert "R2" not in row.detail
    assert "friday-backup" not in row.detail.split("off-site", 1)[1]


def test_set_and_equal_keeps_the_age_rules(status_dir, settings):
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = R2
    write_status(status_dir, hours_ago=30, repository=R2)
    row = _row()
    assert row.status == WARN
    assert "older than HOME_BACKUP_WARN_HOURS" in row.detail
    write_status(status_dir, hours_ago=80, repository=R2)
    assert _row().status == FAIL


def test_a_status_without_repository_warns(status_dir, settings):
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = R2
    write_status(status_dir, hours_ago=3)
    row = _row()
    assert row.status == WARN
    assert "the last backup did not record its repository -- update friday-backup.sh" in (
        row.detail
    )
    # ...and a stale one still fails.
    write_status(status_dir, hours_ago=80)
    assert _row().status == FAIL


@pytest.mark.parametrize(
    ("recorded", "label"),
    [
        (OTHER, "s3:https://s3.eu-central-1.amazonaws.com"),
        ("/mnt/backup-disk/friday-restic", "a local path"),
        ("rest:https://backup.example.net:8000/friday", "rest:https://backup.example.net"),
        ("sftp:backup-host:/srv/restic", "sftp:backup-host"),
        ("b2:friday-bucket:/friday", "b2:friday-bucket"),
        (R2.replace("friday-backup", "another-bucket"), None),
    ],
)
def test_a_different_repository_fails(status_dir, settings, recorded, label):
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = R2
    write_status(status_dir, hours_ago=1, repository=recorded)
    row = _row()
    assert row.status == FAIL
    assert "not the configured off-site repository" in row.detail
    if label is not None:
        assert f"the last backup went to {label}," in row.detail
    # Scheme and host only: never the path (a bucket or a directory).
    assert "/srv/restic" not in row.detail
    assert "/mnt/backup-disk" not in row.detail
    assert "another-bucket" not in row.detail


@pytest.mark.parametrize(
    "value",
    [
        f"s3:https://AKIA:{SECRET}@account.r2.cloudflarestorage.com/friday-backup",
        f"rest:https://friday:{SECRET}@backup.example.net/",
        f"s3:https://account.r2.cloudflarestorage.com/friday-backup?token={SECRET}",
        f"s3:https://account.r2.cloudflarestorage.com/friday backup {SECRET}",
    ],
)
def test_a_configured_value_with_a_credential_fails_without_echoing_it(status_dir, settings, value):
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = value
    write_status(status_dir, hours_ago=1, repository=R2)
    row = _row()
    assert row.status == FAIL
    assert row.detail.startswith("HOME_BACKUP_OFFSITE_REPOSITORY ")
    assert SECRET not in row.detail
    assert "cloudflarestorage" not in row.detail


@pytest.mark.parametrize(
    "value",
    [
        f"s3:https://AKIA:{SECRET}@account.r2.cloudflarestorage.com/friday-backup",
        f"s3:https://account.r2.cloudflarestorage.com/friday-backup?secret={SECRET}",
        f'x" , "{SECRET}',
        "",
        17,
        None,
        ["s3:https://x"],
    ],
)
@pytest.mark.parametrize("configured", ["", R2])
def test_a_recorded_value_with_a_credential_fails_without_echoing_it(
    status_dir, settings, value, configured
):
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = configured
    write_status(status_dir, hours_ago=1, repository=value)
    row = _row()
    assert row.status == FAIL
    assert "repository in the HOME_BACKUP_STATUS_PATH file" in row.detail
    assert SECRET not in row.detail
    assert "cloudflarestorage" not in row.detail


def test_no_detail_ever_holds_a_key(status_dir, settings):
    """Across every outcome the details carry setting names, the dump name,
    the snapshot and scheme+host -- the status file holds no key, and no
    detail invents one."""
    seen = []
    for configured, recorded in [
        ("", None),
        (R2, None),
        (R2, R2),
        (R2, OTHER),
        (f"s3:https://k:{SECRET}@h/b", R2),
        (R2, f"s3:https://k:{SECRET}@h/b"),
    ]:
        settings.HOME_BACKUP_OFFSITE_REPOSITORY = configured
        extra = {} if recorded is None else {"repository": recorded}
        write_status(status_dir, hours_ago=1, **extra)
        seen.append(_row().detail)
    for detail in seen:
        assert SECRET not in detail
        assert "AWS_" not in detail
        assert "RESTIC_PASSWORD" not in detail


def test_a_bad_configured_value_fails_even_before_any_status_file(settings):
    settings.HOME_BACKUP_STATUS_PATH = ""
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = f"s3:https://k:{SECRET}@h/b"
    row = _row()
    assert row.status == FAIL
    assert SECRET not in row.detail


@pytest.mark.parametrize(
    ("value", "label"),
    [
        (R2, "s3:https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com"),
        ("/var/backups/restic", "a local path"),
        ("relative/dir", "a local path"),
        ("./restic", "a local path"),
        ("s3:s3.amazonaws.com/bucket", "s3:s3.amazonaws.com"),
        ("rclone:remote:path", "rclone:remote"),
    ],
)
def test_repository_label(value, label):
    assert preflight.home_repository_label(value) == label


def test_the_setting_exists_and_defaults_to_empty():
    from django.conf import settings as django_settings

    assert hasattr(django_settings, "HOME_BACKUP_OFFSITE_REPOSITORY")
