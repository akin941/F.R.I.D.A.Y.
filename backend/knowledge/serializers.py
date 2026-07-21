from urllib.parse import urlparse

from rest_framework import serializers

from knowledge.models import KnowledgeDocument


class KnowledgeDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeDocument
        # `raw_text`/`content_hash`/lease fields are internal; not exposed —
        # this endpoint is metadata for the upload/list/delete UI, not a way
        # to read document content back out.
        fields = [
            "id",
            "workspace",
            "source_type",
            "title",
            "status",
            "sensitivity",
            "mime_type",
            "byte_size",
            "source_url",
            "error_message",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PastedTextCreateSerializer(serializers.Serializer):
    # `status`, `source_type`, `created_by` and the final workspace object
    # are server-controlled (see services.create_pasted_text_document).
    workspace = serializers.UUIDField()
    title = serializers.CharField(max_length=255)
    content = serializers.CharField(trim_whitespace=False)
    sensitivity = serializers.ChoiceField(
        choices=KnowledgeDocument.Sensitivity.choices,
        default=KnowledgeDocument.Sensitivity.NORMAL,
    )

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("content must not be empty.")
        return value


class FileUploadCreateSerializer(serializers.Serializer):
    workspace = serializers.UUIDField()
    title = serializers.CharField(max_length=255)
    file = serializers.FileField()
    sensitivity = serializers.ChoiceField(
        choices=KnowledgeDocument.Sensitivity.choices,
        default=KnowledgeDocument.Sensitivity.NORMAL,
    )


class UrlCreateSerializer(serializers.Serializer):
    # `status`, `source_type`, `created_by` and the final workspace object
    # are server-controlled (see services.create_url_document). This
    # scheme check is fast client-facing feedback only — it is NOT the
    # security boundary; `knowledge.url_fetch` re-validates scheme, resolved
    # IP, content-type and size from scratch in the worker regardless.
    workspace = serializers.UUIDField()
    title = serializers.CharField(max_length=255)
    source_url = serializers.URLField(max_length=2000)
    sensitivity = serializers.ChoiceField(
        choices=KnowledgeDocument.Sensitivity.choices,
        default=KnowledgeDocument.Sensitivity.NORMAL,
    )

    def validate_source_url(self, value):
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise serializers.ValidationError(f"Unsupported URL scheme: {parsed.scheme!r}.")
        if parsed.username or parsed.password:
            # `knowledge.url_fetch` never reads userinfo (it connects by
            # pinned IP + an explicit `Host` header, never via the URL's
            # authority component), so a pasted `user:token@host` URL buys
            # the submitter nothing functionally — it only persists a
            # credential verbatim into `source_url`, which every workspace
            # reader with access to this document can see (review finding).
            raise serializers.ValidationError(
                "source_url must not contain embedded credentials (user:password@host)."
            )
        if parsed.fragment:
            # Same credential-leak shape as userinfo above, different
            # carrier: a fragment (e.g. an OAuth implicit-flow
            # `#access_token=...`) is never sent to the server by any HTTP
            # client — `knowledge.url_fetch` doesn't even read `.fragment` —
            # so persisting it buys nothing functionally and only exposes it
            # verbatim to every workspace reader (review finding).
            raise serializers.ValidationError("source_url must not contain a fragment (#...).")
        return value
