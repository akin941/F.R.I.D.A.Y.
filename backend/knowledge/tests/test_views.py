import io

import pytest
from django.core.files.uploadhandler import StopUpload
from rest_framework.test import APIClient

from accounts.services import register_user
from audit.models import AuditLog
from knowledge.models import KnowledgeChunk, KnowledgeDocument
from knowledge.services import InvalidUpload, create_file_upload_document
from knowledge.views import _MaxSizeUploadHandler
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

ROLES = [
    WorkspaceMember.Role.OWNER,
    WorkspaceMember.Role.ADMIN,
    WorkspaceMember.Role.MEMBER,
    WorkspaceMember.Role.VIEWER,
]


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def outsider():
    return register_user(email="outsider@test.dev", password=PASSWORD, name="Outsider")


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def user_with_role(workspace, role, email):
    user = register_user(email=email, password=PASSWORD, name=role)
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
    return user


def pasted_text_payload(workspace, **overrides):
    payload = {
        "workspace": str(workspace.id),
        "title": "Doc",
        "content": "Some document content here.",
        "sensitivity": "normal",
    }
    payload.update(overrides)
    return payload


class TestListView:
    def test_requires_auth(self, workspace):
        response = APIClient().get("/api/v1/knowledge/documents", {"workspace": str(workspace.id)})
        assert response.status_code == 401

    def test_requires_workspace_param(self, owner):
        assert client_for(owner).get("/api/v1/knowledge/documents").status_code == 400

    def test_out_of_scope_workspace_is_404(self, workspace, outsider):
        response = client_for(outsider).get(
            "/api/v1/knowledge/documents", {"workspace": str(workspace.id)}
        )
        assert response.status_code == 404

    def test_list_filters_by_status_and_audits(self, workspace, owner):
        client = client_for(owner)
        created = client.post(
            "/api/v1/knowledge/documents/pasted-text",
            pasted_text_payload(workspace),
            format="json",
        ).json()
        response = client.get(
            "/api/v1/knowledge/documents", {"workspace": str(workspace.id), "status": "pending"}
        )
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == [created["id"]]
        entry = AuditLog.objects.get(action="knowledge.read")
        assert entry.metadata["count"] == 1
        assert entry.metadata["document_ids"] == [created["id"]]

    def test_invalid_status_filter_is_400(self, workspace, owner):
        response = client_for(owner).get(
            "/api/v1/knowledge/documents", {"workspace": str(workspace.id), "status": "bogus"}
        )
        assert response.status_code == 400


class TestPastedTextCreate:
    def test_create_populates_chunks_unembedded_and_pending(self, workspace, owner):
        response = client_for(owner).post(
            "/api/v1/knowledge/documents/pasted-text",
            pasted_text_payload(workspace, content="Sentence one. Sentence two."),
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"
        assert body["source_type"] == "pasted_text"
        document = KnowledgeDocument.objects.get(id=body["id"])
        chunks = list(KnowledgeChunk.objects.filter(document=document))
        assert len(chunks) >= 1
        assert all(c.embedding is None for c in chunks)

    def test_ignores_client_supplied_server_fields(self, workspace, owner):
        response = client_for(owner).post(
            "/api/v1/knowledge/documents/pasted-text",
            {
                **pasted_text_payload(workspace),
                "status": "ready",
                "created_by": None,
            },
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"
        assert body["created_by"] == str(owner.id)

    def test_duplicate_content_hash_is_400(self, workspace, owner):
        client = client_for(owner)
        payload = pasted_text_payload(workspace, content="Exact duplicate text.")
        assert (
            client.post(
                "/api/v1/knowledge/documents/pasted-text", payload, format="json"
            ).status_code
            == 201
        )
        response = client.post(
            "/api/v1/knowledge/documents/pasted-text",
            pasted_text_payload(workspace, content="  EXACT   duplicate text. "),
            format="json",
        )
        assert response.status_code == 400

    def test_empty_content_is_400(self, workspace, owner):
        response = client_for(owner).post(
            "/api/v1/knowledge/documents/pasted-text",
            pasted_text_payload(workspace, content="   "),
            format="json",
        )
        assert response.status_code == 400

    def test_oversized_content_is_400(self, workspace, owner, settings):
        settings.KNOWLEDGE_MAX_UPLOAD_BYTES = 10
        response = client_for(owner).post(
            "/api/v1/knowledge/documents/pasted-text",
            pasted_text_payload(workspace, content="x" * 100),
            format="json",
        )
        assert response.status_code == 400
        assert not KnowledgeDocument.objects.exists()

    def test_escaped_non_ascii_content_within_limit_is_not_falsely_rejected(self, workspace, owner):
        """Round-5 Codex review finding: an earlier Content-Length pre-check
        used a fixed JSON-escaping-overhead allowance, but \\uXXXX-escaped
        emoji/Unicode content can expand 3-6x on the wire relative to its
        decoded size — a legitimate, well-under-the-cap document could have
        been falsely rejected before ever being parsed. There is no
        pre-parse guard on this endpoint anymore (see the view's docstring);
        only the decoded-content check in the service applies."""
        content = "Emoji stress test: " + "😀🎉🚀✨💡" * 200  # well under 1MB decoded
        response = client_for(owner).post(
            "/api/v1/knowledge/documents/pasted-text",
            pasted_text_payload(workspace, content=content),
            format="json",
        )
        assert response.status_code == 201

    def test_near_cap_non_ascii_content_is_not_rejected_by_djangos_body_limit(
        self, workspace, owner
    ):
        """Round-6 Codex review finding: ~450k 'é' characters decode to ~900KB
        (under KNOWLEDGE_MAX_UPLOAD_BYTES) but ~2.7MB once JSON-escaped on the
        wire — enough to trip Django's default 2.5MB DATA_UPLOAD_MAX_MEMORY_SIZE
        before create_pasted_text_document's decoded-size check ever runs.
        DATA_UPLOAD_MAX_MEMORY_SIZE is now scaled off KNOWLEDGE_MAX_UPLOAD_BYTES
        in settings to give this legitimate, under-cap document headroom."""
        content = "é" * 440_000  # ~880KB decoded UTF-8, ~2.7MB JSON-escaped
        response = client_for(owner).post(
            "/api/v1/knowledge/documents/pasted-text",
            pasted_text_payload(workspace, content=content),
            format="json",
        )
        assert response.status_code == 201

    def test_content_containing_nul_byte_is_400_not_500(self, workspace, owner):
        """Round-7 Codex review finding: a JSON string can carry a \\u0000
        escape through to a Python str with no decode error, but PostgreSQL
        text columns cannot store a NUL byte — without an explicit check,
        this reaches the INSERT and fails as a 500 instead of a clean,
        intended 400."""
        response = client_for(owner).post(
            "/api/v1/knowledge/documents/pasted-text",
            pasted_text_payload(workspace, content="before\x00after"),
            format="json",
        )
        assert response.status_code == 400
        assert not KnowledgeDocument.objects.exists()

    def test_create_is_audited(self, workspace, owner):
        client_for(owner).post(
            "/api/v1/knowledge/documents/pasted-text",
            pasted_text_payload(workspace),
            format="json",
        )
        entry = AuditLog.objects.get(action="knowledge.uploaded")
        assert entry.metadata["source_type"] == "pasted_text"
        assert "content" not in entry.metadata


class TestFileUploadCreate:
    def _upload(self, client, workspace, content=b"File body text.", filename="doc.txt", **extra):
        payload = {
            "workspace": str(workspace.id),
            "title": "Uploaded",
            "file": io.BytesIO(content),
            "sensitivity": "normal",
        }
        payload["file"].name = filename
        payload.update(extra)
        return client.post("/api/v1/knowledge/documents/file-upload", payload, format="multipart")

    def test_valid_text_file_creates_document(self, workspace, owner):
        response = self._upload(client_for(owner), workspace)
        assert response.status_code == 201
        body = response.json()
        assert body["source_type"] == "file_upload"
        assert body["mime_type"] == "text/plain"

    def test_oversized_file_is_400(self, workspace, owner, settings):
        settings.KNOWLEDGE_MAX_UPLOAD_BYTES = 10
        response = self._upload(client_for(owner), workspace, content=b"x" * 100)
        assert response.status_code == 400

    def test_disallowed_mime_type_is_400(self, workspace, owner):
        response = self._upload(
            client_for(owner), workspace, filename="doc.png", content=b"\x89PNG\r\n"
        )
        assert response.status_code == 400

    def test_file_containing_nul_byte_is_400_not_500(self, workspace, owner):
        """Round-7 Codex review finding: a NUL byte survives UTF-8 decoding
        but PostgreSQL text columns cannot store one — without an explicit
        check, this reaches the INSERT and fails as a 500 instead of a
        clean, intended 400."""
        response = self._upload(client_for(owner), workspace, content=b"before\x00after")
        assert response.status_code == 400
        assert not KnowledgeDocument.objects.exists()


class TestUrlCreate:
    """The `url` endpoint only creates a `pending` document — the real fetch
    happens in the branch-3 ingestion worker (test_tasks.py), never in this
    request/response path. These are plain `django_db` tests (no
    `transaction=True`), so `on_commit` dispatch never actually fires here
    (see knowledge/tests/test_jobs.py's identical note) — no network-call
    risk from this file."""

    def test_creates_pending_document(self, workspace, owner):
        response = client_for(owner).post(
            "/api/v1/knowledge/documents/url",
            {
                "workspace": str(workspace.id),
                "title": "A page",
                "source_url": "https://example.com/article",
                "sensitivity": "normal",
            },
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["source_type"] == "url"
        assert body["status"] == "pending"
        assert body["source_url"] == "https://example.com/article"

    def test_disallowed_scheme_is_400(self, workspace, owner):
        response = client_for(owner).post(
            "/api/v1/knowledge/documents/url",
            {
                "workspace": str(workspace.id),
                "title": "FTP thing",
                "source_url": "ftp://example.com/file",
                "sensitivity": "normal",
            },
            format="json",
        )
        assert response.status_code == 400
        assert not KnowledgeDocument.objects.exists()

    def test_embedded_credentials_are_rejected(self, workspace, owner):
        """Round-6 Codex review finding: a pasted `user:token@host` URL
        would otherwise be stored verbatim in `source_url` and exposed to
        every workspace reader via the document serializer — the fetcher
        itself never reads userinfo, so persisting it buys nothing."""
        response = client_for(owner).post(
            "/api/v1/knowledge/documents/url",
            {
                "workspace": str(workspace.id),
                "title": "Leaky URL",
                "source_url": "https://user:token@example.com/page",
                "sensitivity": "normal",
            },
            format="json",
        )
        assert response.status_code == 400
        assert not KnowledgeDocument.objects.exists()

    def test_url_fragment_is_rejected(self, workspace, owner):
        """Round-9 Codex review finding: same credential-leak shape as
        userinfo above, different carrier — a fragment (e.g. an OAuth
        implicit-flow `#access_token=...`) is never sent to the server by
        any HTTP client, so persisting it in `source_url` only exposes it
        verbatim to every workspace reader."""
        response = client_for(owner).post(
            "/api/v1/knowledge/documents/url",
            {
                "workspace": str(workspace.id),
                "title": "Leaky fragment",
                "source_url": "https://example.com/callback#access_token=secret123",
                "sensitivity": "normal",
            },
            format="json",
        )
        assert response.status_code == 400
        assert not KnowledgeDocument.objects.exists()

    def test_create_is_audited(self, workspace, owner):
        client_for(owner).post(
            "/api/v1/knowledge/documents/url",
            {
                "workspace": str(workspace.id),
                "title": "A page",
                "source_url": "https://example.com/article",
                "sensitivity": "normal",
            },
            format="json",
        )
        entry = AuditLog.objects.get(action="knowledge.uploaded")
        assert entry.metadata["source_type"] == "url"


class TestMaxSizeUploadHandler:
    """Round-4 Codex review finding: a chunked-transfer-encoded request has
    no Content-Length header, so a pre-parse header check is a no-op for it —
    the cap must be enforced from bytes actually received during streaming,
    not from a declared (and possibly absent) length."""

    def test_aborts_once_cumulative_bytes_exceed_max_size(self):
        handler = _MaxSizeUploadHandler(request=None, max_size=10)
        handler.receive_data_chunk(b"12345", 0)  # 5 bytes, under the cap
        assert handler.aborted is False
        with pytest.raises(StopUpload):
            handler.receive_data_chunk(b"1234567", 5)  # 12 bytes total, over
        assert handler.aborted is True

    def test_does_not_abort_at_or_under_max_size(self):
        handler = _MaxSizeUploadHandler(request=None, max_size=10)
        handler.receive_data_chunk(b"1234567890", 0)  # exactly the cap
        assert handler.aborted is False


class TestFileUploadMimeExtensionAllowlist:
    """Round-2 Codex review finding: MIME type and filename extension are
    both client-controlled and independently spoofable, so the service must
    reject unless *both* are on the allowlist — checking only one lets a
    mismatched pair (e.g. a disallowed extension with a forged
    Content-Type) through."""

    def test_mismatched_extension_is_rejected_even_with_allowed_mime(self, workspace, owner):
        with pytest.raises(InvalidUpload):
            create_file_upload_document(
                workspace=workspace,
                actor=owner,
                title="Doc",
                file_bytes=b"safe looking content",
                mime_type="text/plain",
                filename="doc.exe",
                sensitivity=KnowledgeDocument.Sensitivity.NORMAL,
            )

    def test_mismatched_mime_is_rejected_even_with_allowed_extension(self, workspace, owner):
        with pytest.raises(InvalidUpload):
            create_file_upload_document(
                workspace=workspace,
                actor=owner,
                title="Doc",
                file_bytes=b"safe looking content",
                mime_type="application/octet-stream",
                filename="doc.txt",
                sensitivity=KnowledgeDocument.Sensitivity.NORMAL,
            )


class TestPermissionMatrix:
    """Role × endpoint matrix (CLAUDE.md rule 7)."""

    @pytest.mark.parametrize("role", ROLES)
    def test_all_roles_can_read(self, workspace, owner, role):
        client_for(owner).post(
            "/api/v1/knowledge/documents/pasted-text",
            pasted_text_payload(workspace),
            format="json",
        )
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        response = client_for(user).get(
            "/api/v1/knowledge/documents", {"workspace": str(workspace.id)}
        )
        assert response.status_code == 200
        assert len(response.json()) == 1

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 201),
            (WorkspaceMember.Role.ADMIN, 201),
            (WorkspaceMember.Role.MEMBER, 201),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_upload(self, workspace, role, expected):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        response = client_for(user).post(
            "/api/v1/knowledge/documents/pasted-text",
            pasted_text_payload(workspace),
            format="json",
        )
        assert response.status_code == expected

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 201),
            (WorkspaceMember.Role.ADMIN, 201),
            (WorkspaceMember.Role.MEMBER, 201),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_file_upload(self, workspace, role, expected):
        """The file-upload endpoint is its own API surface (own URL, own
        view class) and needs its own role x endpoint coverage — it isn't
        exercised by test_upload above, which only hits pasted-text (review
        finding)."""
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        payload = {
            "workspace": str(workspace.id),
            "title": "Uploaded",
            "file": io.BytesIO(b"File body text."),
            "sensitivity": "normal",
        }
        payload["file"].name = "doc.txt"
        response = client_for(user).post(
            "/api/v1/knowledge/documents/file-upload", payload, format="multipart"
        )
        assert response.status_code == expected

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 201),
            (WorkspaceMember.Role.ADMIN, 201),
            (WorkspaceMember.Role.MEMBER, 201),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_url(self, workspace, role, expected):
        """The url endpoint is its own API surface too — needs its own
        role x endpoint coverage, same rationale as test_file_upload above."""
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        response = client_for(user).post(
            "/api/v1/knowledge/documents/url",
            {
                "workspace": str(workspace.id),
                "title": "A page",
                "source_url": "https://example.com/article",
                "sensitivity": "normal",
            },
            format="json",
        )
        assert response.status_code == expected

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 204),
            (WorkspaceMember.Role.ADMIN, 204),
            (WorkspaceMember.Role.MEMBER, 403),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_delete_foreign_upload(self, workspace, owner, role, expected):
        created = (
            client_for(owner)
            .post(
                "/api/v1/knowledge/documents/pasted-text",
                pasted_text_payload(workspace),
                format="json",
            )
            .json()
        )
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        response = client_for(user).delete(f"/api/v1/knowledge/documents/{created['id']}")
        assert response.status_code == expected

    def test_member_deletes_own_upload(self, workspace):
        member = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "m@test.dev")
        created = (
            client_for(member)
            .post(
                "/api/v1/knowledge/documents/pasted-text",
                pasted_text_payload(workspace),
                format="json",
            )
            .json()
        )
        response = client_for(member).delete(f"/api/v1/knowledge/documents/{created['id']}")
        assert response.status_code == 204


class TestDetailView:
    def test_out_of_scope_detail_is_404_not_403(self, workspace, owner, outsider):
        created = (
            client_for(owner)
            .post(
                "/api/v1/knowledge/documents/pasted-text",
                pasted_text_payload(workspace),
                format="json",
            )
            .json()
        )
        client = client_for(outsider)
        assert client.get(f"/api/v1/knowledge/documents/{created['id']}").status_code == 404
        assert client.delete(f"/api/v1/knowledge/documents/{created['id']}").status_code == 404

    def test_detail_read_is_audited(self, workspace, owner):
        created = (
            client_for(owner)
            .post(
                "/api/v1/knowledge/documents/pasted-text",
                pasted_text_payload(workspace),
                format="json",
            )
            .json()
        )
        client_for(owner).get(f"/api/v1/knowledge/documents/{created['id']}")
        entry = AuditLog.objects.get(action="knowledge.read")
        assert entry.metadata == {"document_ids": [created["id"]], "count": 1}

    def test_delete_removes_document_and_chunks(self, workspace, owner):
        created = (
            client_for(owner)
            .post(
                "/api/v1/knowledge/documents/pasted-text",
                pasted_text_payload(workspace),
                format="json",
            )
            .json()
        )
        document_id = created["id"]
        assert KnowledgeChunk.objects.filter(document_id=document_id).exists()
        client_for(owner).delete(f"/api/v1/knowledge/documents/{document_id}")
        assert not KnowledgeDocument.objects.filter(id=document_id).exists()
        assert not KnowledgeChunk.objects.filter(document_id=document_id).exists()

    def test_delete_is_audited(self, workspace, owner):
        created = (
            client_for(owner)
            .post(
                "/api/v1/knowledge/documents/pasted-text",
                pasted_text_payload(workspace),
                format="json",
            )
            .json()
        )
        client_for(owner).delete(f"/api/v1/knowledge/documents/{created['id']}")
        entry = AuditLog.objects.get(action="knowledge.deleted")
        assert entry.resource_id == created["id"]
        assert "content" not in entry.metadata
