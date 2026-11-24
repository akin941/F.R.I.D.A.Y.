"""Phase 12 D13: uploads -- one SKILL.md or a zip bundle. Scripts,
binaries, dotfiles, symlinks, `..` and absolute paths, too many or too
large references and nested archives refuse the whole upload; the zip
limits are checked from the central directory before any entry is read;
nothing is stored for a refused upload."""

import zipfile

import pytest

from skills.models import InstructionSkill, InstructionSkillVersion
from skills.skill_md import SkillBundleError, SkillParseError, read_bundle
from skills.tests.helpers import (
    BASE,
    make_owner,
    make_zip,
    skill_text,
    symlink_info,
    under_declare,
)

SKILL = skill_text(body="Bak: references/checklist.md").encode()
REF = b"# Checklist\n- testler\n"


def bundle_code(data, filename="skill.zip"):
    with pytest.raises(SkillBundleError) as exc_info:
        read_bundle(data, filename=filename)
    return exc_info.value.code


def test_a_single_skill_md():
    parsed = read_bundle(SKILL, filename="SKILL.md")
    assert parsed.name == "pr-yaz"


def test_a_zip_with_references():
    data = make_zip([("SKILL.md", SKILL), ("references/", b""), ("references/checklist.md", REF)])
    parsed = read_bundle(data, filename="skill.zip")
    assert parsed.references == {"references/checklist.md": REF.decode()}


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("run.sh", "host_execution_refused"),
        ("references/tool.py", "host_execution_refused"),
        ("helper.js", "host_execution_refused"),
        ("app.exe", "host_execution_refused"),
        (".env", "bundle_refused"),
        ("references/.hidden.md", "bundle_refused"),
        ("../escape.md", "bundle_refused"),
        ("references/../../x.md", "bundle_refused"),
        ("/etc/passwd.md", "bundle_refused"),
        ("C:/x.md", "bundle_refused"),
        ("inner.zip", "bundle_refused"),
        ("notes.txt", "bundle_refused"),
        ("references/sub/deep.md", "bundle_refused"),
    ],
)
def test_forbidden_entries_refuse_the_whole_upload(name, code):
    data = make_zip([("SKILL.md", SKILL), (name, b"echo hi\n")])
    assert bundle_code(data) == code


def test_a_binary_reference_is_refused():
    data = make_zip([("SKILL.md", SKILL), ("references/x.md", b"\x00\x01\x02")])
    assert bundle_code(data) == "host_execution_refused"


def test_a_symlink_is_refused():
    data = make_zip([("SKILL.md", SKILL), (symlink_info("references/link.md"), b"/etc/passwd")])
    assert bundle_code(data) == "bundle_refused"


def test_more_than_ten_references_is_refused():
    entries = [("SKILL.md", SKILL)] + [(f"references/r{i}.md", b"x") for i in range(11)]
    assert bundle_code(make_zip(entries)) == "bundle_refused"


def test_an_oversize_reference_is_refused():
    data = make_zip(
        [("SKILL.md", SKILL), ("references/big.md", b"a b\n" * 17000)],
        compression=zipfile.ZIP_STORED,
    )
    assert bundle_code(data) == "bundle_too_large"


def test_no_skill_md_is_refused():
    assert bundle_code(make_zip([("references/a.md", REF)])) == "bundle_refused"


def test_a_script_upload_is_refused():
    assert bundle_code(b"#!/bin/sh\nrm -rf /\n", filename="install.sh") == (
        "host_execution_refused"
    )


def test_a_binary_single_file_is_refused():
    assert bundle_code(b"\x7fELF\x00\x00", filename="SKILL.md") == "bundle_refused"


def test_a_not_a_zip_is_refused():
    assert bundle_code(b"PK\x03\x04garbage") == "bundle_refused"


# --- zip limits: refused from the central directory, before any read --------


@pytest.fixture
def no_entry_reads(monkeypatch):
    """Call it after building the zip: from then on, opening any entry body
    fails the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("an entry was opened before the limits were checked")

    return lambda: monkeypatch.setattr(zipfile.ZipFile, "open", refuse)


def test_upload_over_the_byte_limit(settings, no_entry_reads):
    settings.SKILLS_BUNDLE_MAX_BYTES = 1000
    no_entry_reads()
    assert bundle_code(b"x" * 1001, filename="SKILL.md") == "bundle_too_large"


def test_more_than_twelve_entries(no_entry_reads):
    entries = [("SKILL.md", SKILL)] + [(f"references/r{i}.md", b"x") for i in range(12)]
    data = make_zip(entries)
    no_entry_reads()
    assert bundle_code(data) == "bundle_too_large"


def test_declared_total_over_one_megabyte(no_entry_reads):
    data = make_zip([("SKILL.md", SKILL), ("references/a.md", b"x")])
    lying = under_declare(data, "references/a.md", 2 * 1024 * 1024)
    no_entry_reads()
    assert bundle_code(lying) == "bundle_too_large"


def test_a_zip_bomb_entry(no_entry_reads):
    data = make_zip([("SKILL.md", SKILL), ("references/bomb.md", b"\0" * 60000)])
    no_entry_reads()
    assert bundle_code(data) == "bundle_too_large"


def test_an_under_declared_entry_stops_the_read():
    data = make_zip(
        [("SKILL.md", SKILL), ("references/checklist.md", REF * 50)],
        compression=zipfile.ZIP_STORED,
    )
    lying = under_declare(data, "references/checklist.md", 10)
    reads = []
    real_open = zipfile.ZipFile.open

    def counting_open(self, info, *args, **kwargs):
        handle = real_open(self, info, *args, **kwargs)
        real_read = handle.read

        def read(n=-1):
            chunk = real_read(n)
            reads.append((info.filename, info.file_size, len(chunk)))
            return chunk

        handle.read = read
        return handle

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(zipfile.ZipFile, "open", counting_open)
        assert bundle_code(lying) == "bundle_too_large"
    read_of_lie = sum(n for name, _, n in reads if name == "references/checklist.md")
    assert read_of_lie <= 10


def test_a_bundle_whose_skill_md_is_refused_raises_the_parser_error():
    bad = b"---\nname: ab\ndescription: x\nallowed-tools: Bash\n---\nbody\n"
    with pytest.raises(SkillParseError):
        read_bundle(make_zip([("SKILL.md", bad)]), filename="s.zip")


# --- through the upload endpoint: nothing stored --------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("entries", "code"),
    [
        ([("SKILL.md", SKILL), ("run.sh", b"x")], "skill.host_execution_refused"),
        ([("SKILL.md", SKILL), ("../x.md", b"x")], "skill.bundle_refused"),
        ([("SKILL.md", SKILL), ("references/bomb.md", b"\0" * 60000)], "skill.bundle_too_large"),
        (
            [("SKILL.md", b"---\nname: ab\ndescription: x\ntools: all\n---\nb\n")],
            "skill.invalid",
        ),
    ],
)
def test_refused_uploads_store_nothing(web_client, entries, code):
    from django.core.files.uploadedfile import SimpleUploadedFile

    owner = make_owner()
    upload = SimpleUploadedFile("skill.zip", make_zip(entries), content_type="application/zip")
    response = web_client(owner).post(f"{BASE}/upload", {"file": upload}, format="multipart")
    assert response.status_code == 400, response.content
    assert response.data["code"] == code
    assert not InstructionSkill.objects.exists()
    assert not InstructionSkillVersion.objects.exists()


@pytest.mark.django_db
def test_the_request_body_limit_refuses_before_parsing(web_client, settings):
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.SKILLS_BUNDLE_MAX_BYTES = 1000
    owner = make_owner()
    upload = SimpleUploadedFile("SKILL.md", b"x" * 200_000, content_type="text/markdown")
    response = web_client(owner).post(f"{BASE}/upload", {"file": upload}, format="multipart")
    assert response.status_code == 400
    assert response.data["code"] == "skill.bundle_too_large"
    assert not InstructionSkill.objects.exists()


@pytest.mark.django_db
def test_a_good_upload_is_stored_proposed(web_client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    owner = make_owner()
    data = make_zip([("SKILL.md", SKILL), ("references/checklist.md", REF)])
    upload = SimpleUploadedFile("skill.zip", data, content_type="application/zip")
    response = web_client(owner).post(f"{BASE}/upload", {"file": upload}, format="multipart")
    assert response.status_code == 201, response.content
    skill = InstructionSkill.objects.get()
    assert skill.status == "proposed" and skill.origin == "upload"
    assert response.data["decision"] == "pending"
    assert response.data["references"] == {"references/checklist.md": REF.decode()}
