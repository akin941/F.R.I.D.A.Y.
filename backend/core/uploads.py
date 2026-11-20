"""Streaming upload-size guard shared by every multipart endpoint
(moved from `knowledge/views.py` in Phase 6 branch 2, brief D2: the
knowledge file upload and the voice transcription endpoint both use it)."""

from django.core.files.uploadhandler import FileUploadHandler, StopUpload


class MaxSizeUploadHandler(FileUploadHandler):
    """Aborts multipart parsing mid-stream once `max_size` bytes have been
    received, instead of relying on a Content-Length header (round-4 Codex
    review finding: a chunked-transfer-encoded request has no Content-Length,
    so a pre-parse check is a no-op for it, and the request would otherwise
    be fully parsed/spooled to temp disk before any check ran).

    Placed first in `request.upload_handlers`: it only counts bytes and
    passes each chunk through unmodified, so the real handler behind it
    (memory/temp-file) still materializes the file when the cap isn't hit.
    `file_complete` always returns `None` -- this handler never produces a
    file itself, it only gates the ones that do.
    """

    def __init__(self, request, *, max_size):
        super().__init__(request)
        self.max_size = max_size
        self.received_bytes = 0
        self.aborted = False

    def receive_data_chunk(self, raw_data, start):
        self.received_bytes += len(raw_data)
        if self.received_bytes > self.max_size:
            self.aborted = True
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None
