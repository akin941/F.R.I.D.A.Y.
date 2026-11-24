"""Shared helpers for the Phase 12 branch 5 skill tests."""

import io
import stat
import struct
import zipfile

from accounts.services import register_user
from workspaces.models import Workspace, WorkspaceMember

PASSWORD = "Str0ngDevPass!42"
BASE = "/api/v1/skills"


def skill_text(name="pr-yaz", description="PR açıklaması yazarken kullan.", body=None):
    body = body or "Önce bir cümlelik özet yaz. Sonra değişiklikleri listele."
    return f'---\nname: {name}\ndescription: "{description}"\n---\n{body}\n'


def make_owner(email="owner@test.dev"):
    user = register_user(email=email, password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


def personal_of(user):
    return Workspace.objects.get(owner=user, type=Workspace.Type.PERSONAL)


def make_firm(owner, member):
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.MEMBER)
    return firm


def make_zip(entries, *, compression=zipfile.ZIP_DEFLATED):
    """entries: list of (name, bytes) or (ZipInfo, bytes)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def symlink_info(name):
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def under_declare(data, name, declared):
    """Rewrite the central-directory uncompressed size of `name` to
    `declared` (a lying header)."""
    raw = bytearray(data)
    index = 0
    encoded = name.encode()
    while True:
        index = raw.find(b"PK\x01\x02", index)
        if index < 0:
            raise AssertionError("entry not found")
        name_len = struct.unpack("<H", raw[index + 28 : index + 30])[0]
        if bytes(raw[index + 46 : index + 46 + name_len]) == encoded:
            raw[index + 24 : index + 28] = struct.pack("<I", declared)
            return bytes(raw)
        index += 4


def approve(version, owner):
    from core.channels import CHANNEL_WEB
    from skills import services

    return services.approve_version(
        actor=owner, channel=CHANNEL_WEB, version=version, expected_sha256=version.content_sha256
    )


def propose(workspace, owner, text=None, origin="upload"):
    from core.channels import CHANNEL_WEB
    from skills import services

    parsed = services.parse_text(text or skill_text())
    return services.propose_from_owner(
        actor=owner, channel=CHANNEL_WEB, workspace=workspace, parsed=parsed, origin=origin
    )


def active_skill(workspace, owner, text=None):
    version = propose(workspace, owner, text)
    approve(version, owner)
    version.skill.refresh_from_db()
    return version.skill
