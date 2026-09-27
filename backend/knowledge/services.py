"""Knowledge document services — the only sanctioned write path for
KnowledgeDocument/KnowledgeChunk rows (MVP 3 §2).

Role policy (simpler than Memory's — no AI-suggested/approval axis):

    action        owner/admin   member         viewer
    read/list     yes           yes            yes
    upload        yes           yes            no
    delete        yes           own uploads    no

"Own uploads" is the exact predicate `created_by=actor`, same shape as
Memory's member-authorization predicate.

Approval gate: explicitly out of scope, by the same precedent MVP 2 already
established for user-created memories (CLAUDE.md rule 4 targets *agent*-
initiated writes; a human directly uploading a document is not that).
"""

import pathlib

from django.conf import settings
from django.db import IntegrityError, transaction

from audit.services import record_audit
from knowledge.chunking import chunk_text, estimate_token_count
from knowledge.jobs import dispatch_knowledge_document
from knowledge.models import KnowledgeChunk, KnowledgeDocument, compute_content_hash
from workspaces.models import WorkspaceMember

_AUDIT_ID_CAP = 200

_CURATOR_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}
_WRITER_ROLES = _CURATOR_ROLES | {WorkspaceMember.Role.MEMBER}

# Plain-text/markdown only in branch 2 — PDF/docx/etc. is a later MVP (brief
# "Out of scope"). Both the MIME type AND the filename extension must be on
# their respective allowlists — either one alone is client-controlled and
# spoofable (a browser sends whatever Content-Type the caller asks for; a
# filename is just a string), so neither is trusted on its own.
ALLOWED_UPLOAD_MIME_TYPES = {"text/plain", "text/markdown"}
ALLOWED_UPLOAD_EXTENSIONS = {".txt", ".md"}


class KnowledgeServiceError(Exception):
    """Base class; views translate subclasses to HTTP statuses."""


class KnowledgePermissionDenied(KnowledgeServiceError):
    """Actor's role does not allow the action → 403."""

    code = "knowledge.permission_denied"


class DuplicateDocument(KnowledgeServiceError):
    """content_hash collision inside the workspace → 400."""

    code = "knowledge.duplicate"


class InvalidUpload(KnowledgeServiceError):
    """Bad size/MIME/content → 400."""

    code = "knowledge.invalid_upload"


def get_role(actor, workspace):
    membership = (
        WorkspaceMember.objects.for_actor(actor, workspace=workspace).filter(user=actor).first()
    )
    return membership.role if membership else None


def _is_own_upload(document, actor):
    return document.created_by_id is not None and document.created_by_id == actor.id


def _require(condition, action):
    if not condition:
        raise KnowledgePermissionDenied(f"Role does not allow {action}.")


def content_hash_exists(workspace, content_hash):
    """Public: also used by the branch-3 ingestion task, which must run this
    same dedup check on a `url` document's content AFTER fetching it (the
    hash cannot be known before then)."""
    return KnowledgeDocument.objects.filter(workspace=workspace, content_hash=content_hash).exists()


def _check_content_hash_free(workspace, content_hash):
    if content_hash_exists(workspace, content_hash):
        raise DuplicateDocument("An equivalent document already exists in this workspace.")


def reject_nul_bytes(content):
    """Public: also used by the branch-3 ingestion task for fetched `url`
    content. PostgreSQL text columns cannot store a NUL byte at all —
    successful UTF-8 decoding (file uploads/fetched URLs) or a JSON string
    escape (pasted text) can all still carry one through, so without this
    check the later INSERT would fail with a database error instead of a
    clean, intended failure (round-7 Codex review finding)."""
    if "\x00" in content:
        raise InvalidUpload("content must not contain NUL bytes.")


def _create_chunks(document, text):
    pieces = chunk_text(text)
    KnowledgeChunk.objects.bulk_create(
        [
            KnowledgeChunk(
                workspace=document.workspace,
                document=document,
                ordinal=index,
                content=piece,
                token_count=estimate_token_count(piece),
            )
            for index, piece in enumerate(pieces)
        ]
    )


@transaction.atomic
def create_pasted_text_document(*, workspace, actor, title, content, sensitivity):
    """Text pasted directly: content is already in hand, so raw_text/
    content_hash are populated and chunking happens synchronously, in this
    same transaction — there is no reason to defer deterministic, no-network
    work to a background task."""
    _require(get_role(actor, workspace) in _WRITER_ROLES, "uploading documents")
    if not content.strip():
        raise InvalidUpload("content must not be empty.")
    reject_nul_bytes(content)
    byte_size = len(content.encode())
    max_bytes = settings.KNOWLEDGE_MAX_UPLOAD_BYTES
    if byte_size > max_bytes:
        raise InvalidUpload(f"Content exceeds the {max_bytes}-byte upload limit.")
    content_hash = compute_content_hash(content)
    _check_content_hash_free(workspace, content_hash)
    try:
        document = KnowledgeDocument.objects.create(
            workspace=workspace,
            source_type=KnowledgeDocument.SourceType.PASTED_TEXT,
            title=title,
            status=KnowledgeDocument.Status.PENDING,
            sensitivity=sensitivity,
            mime_type="text/plain",
            byte_size=byte_size,
            raw_text=content,
            content_hash=content_hash,
            created_by=actor,
        )
    except IntegrityError:
        # Constraint backstop for a concurrent create that won the race
        # between the pre-check above and this insert (mirrors
        # memory.services.create_memory's identical pattern) — not a catch-
        # all for unrelated integrity errors, which the pre-check above
        # already ruled out for the one constraint this model has.
        raise DuplicateDocument(
            "An equivalent document already exists in this workspace."
        ) from None
    _create_chunks(document, content)
    transaction.on_commit(lambda: dispatch_knowledge_document(document.id))
    _record(document, "knowledge.uploaded", actor)
    return document


@transaction.atomic
def create_file_upload_document(
    *, workspace, actor, title, file_bytes, mime_type, filename, sensitivity
):
    """Plain-text/markdown file upload only in this branch (max size +
    MIME/extension allowlist enforced here, not just at the view layer, so
    the service is safe to call directly from tests/other callers)."""
    _require(get_role(actor, workspace) in _WRITER_ROLES, "uploading documents")
    extension = pathlib.Path(filename or "").suffix.lower()
    if mime_type not in ALLOWED_UPLOAD_MIME_TYPES or extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise InvalidUpload(f"Unsupported file type: {mime_type!r} ({filename!r}).")
    max_bytes = settings.KNOWLEDGE_MAX_UPLOAD_BYTES
    if len(file_bytes) > max_bytes:
        raise InvalidUpload(f"File exceeds the {max_bytes}-byte upload limit.")
    try:
        content = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidUpload("File must be UTF-8 encoded text.") from None
    if not content.strip():
        raise InvalidUpload("content must not be empty.")
    reject_nul_bytes(content)
    content_hash = compute_content_hash(content)
    _check_content_hash_free(workspace, content_hash)
    try:
        document = KnowledgeDocument.objects.create(
            workspace=workspace,
            source_type=KnowledgeDocument.SourceType.FILE_UPLOAD,
            title=title,
            status=KnowledgeDocument.Status.PENDING,
            sensitivity=sensitivity,
            mime_type=mime_type,
            byte_size=len(file_bytes),
            raw_text=content,
            content_hash=content_hash,
            created_by=actor,
        )
    except IntegrityError:
        # Constraint backstop for a concurrent create that won the race
        # between the pre-check above and this insert — see the identical
        # comment in create_pasted_text_document.
        raise DuplicateDocument(
            "An equivalent document already exists in this workspace."
        ) from None
    _create_chunks(document, content)
    transaction.on_commit(lambda: dispatch_knowledge_document(document.id))
    _record(document, "knowledge.uploaded", actor)
    return document


@transaction.atomic
def create_url_document(*, workspace, actor, title, source_url, sensitivity):
    """`url` source: content is not in hand yet (fetch is network I/O, so it
    must not run in this request/response transaction) — the document is
    created `pending` with `raw_text`/`content_hash` still null, and the
    dispatched ingestion task (branch 3) fetches, chunks, dedup-checks and
    embeds it. No size/MIME/dedup validation happens here; all of it moves
    to the task, since none of it is knowable before the fetch succeeds."""
    _require(get_role(actor, workspace) in _WRITER_ROLES, "uploading documents")
    document = KnowledgeDocument.objects.create(
        workspace=workspace,
        source_type=KnowledgeDocument.SourceType.URL,
        title=title,
        status=KnowledgeDocument.Status.PENDING,
        sensitivity=sensitivity,
        source_url=source_url,
        created_by=actor,
    )
    transaction.on_commit(lambda: dispatch_knowledge_document(document.id))
    _record(document, "knowledge.uploaded", actor)
    return document


@transaction.atomic
def delete_document(*, document, actor):
    role = get_role(actor, document.workspace)
    _require(
        role in _CURATOR_ROLES or (role in _WRITER_ROLES and _is_own_upload(document, actor)),
        "deleting this document",
    )
    _record(document, "knowledge.deleted", actor)
    document.delete()


def record_read(*, actor, workspace, documents):
    """Read audit (mirrors `memory.services.record_read`): every access is
    auditable, ids + count only, no content."""
    all_ids = [str(d.id) for d in documents]
    chunks = [all_ids[i : i + _AUDIT_ID_CAP] for i in range(0, len(all_ids), _AUDIT_ID_CAP)] or [[]]
    for index, chunk in enumerate(chunks):
        metadata = {"document_ids": chunk, "count": len(all_ids)}
        if len(chunks) > 1:
            metadata["chunk"] = index + 1
            metadata["chunks"] = len(chunks)
        record_audit(
            action="knowledge.read",
            actor=actor,
            workspace=workspace,
            metadata=metadata,
        )


def _record(document, action, actor, **metadata):
    record_audit(
        action=action,
        actor=actor,
        workspace=document.workspace,
        resource=document,
        metadata={"status": document.status, "source_type": document.source_type, **metadata},
    )
