"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  KNOWLEDGE_SENSITIVITY_LABELS,
  KNOWLEDGE_SOURCE_TYPE_LABELS,
  KNOWLEDGE_SOURCE_TYPES,
} from "@/components/knowledge/labels";
import { apiPostForm, apiPostJson, ApiError } from "@/lib/api-client";
import type { ApiKnowledgeDocument, KnowledgeSensitivity, KnowledgeSourceType } from "@/types/api";

// Knowledge create errors can come back either flat (`{detail}`, matching
// every other BFF route in this app) or field-keyed (DRF's default
// `serializer.is_valid(raise_exception=True)` shape, e.g.
// `{content: ["content must not be empty."]}`) — pasted-text/url validation
// failures use the latter. Fall back to the first field-level message
// before giving up, so those don't silently render as a generic error.
function extractErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (typeof err.body.detail === "string") return err.body.detail;
    for (const value of Object.values(err.body)) {
      if (Array.isArray(value) && typeof value[0] === "string") return value[0];
    }
    return fallback;
  }
  return "Something went wrong. Please try again.";
}

export function KnowledgeCreateForm({
  workspaceId,
  onCreated,
  onCancel,
}: {
  workspaceId: string;
  onCreated: (document: ApiKnowledgeDocument) => void;
  onCancel: () => void;
}) {
  const [sourceType, setSourceType] = useState<KnowledgeSourceType>("pasted_text");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [sensitivity, setSensitivity] = useState<KnowledgeSensitivity>("normal");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (sourceType === "file_upload" && !file) {
      setError("Choose a file to upload.");
      return;
    }

    setSubmitting(true);
    try {
      let document: ApiKnowledgeDocument;
      if (sourceType === "pasted_text") {
        document = await apiPostJson<ApiKnowledgeDocument>(
          "/api/knowledge/documents/pasted-text",
          { workspace: workspaceId, title, content, sensitivity },
        );
      } else if (sourceType === "url") {
        document = await apiPostJson<ApiKnowledgeDocument>("/api/knowledge/documents/url", {
          workspace: workspaceId,
          title,
          source_url: sourceUrl,
          sensitivity,
        });
      } else {
        const formData = new FormData();
        formData.set("workspace", workspaceId);
        formData.set("title", title);
        formData.set("file", file as File);
        formData.set("sensitivity", sensitivity);
        document = await apiPostForm<ApiKnowledgeDocument>(
          "/api/knowledge/documents/file-upload",
          formData,
        );
      }
      onCreated(document);
      setTitle("");
      setContent("");
      setSourceUrl("");
      setFile(null);
    } catch (err) {
      setError(extractErrorMessage(err, "That document could not be created."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>New document</CardTitle>
      </CardHeader>
      <form onSubmit={handleSubmit}>
        <CardContent className="flex flex-col gap-3">
          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <div className="flex flex-col gap-2">
            <Label htmlFor="create-knowledge-source-type">Source</Label>
            <Select
              value={sourceType}
              onValueChange={(value) => setSourceType(value as KnowledgeSourceType)}
            >
              <SelectTrigger id="create-knowledge-source-type" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KNOWLEDGE_SOURCE_TYPES.map((type) => (
                  <SelectItem key={type} value={type}>
                    {KNOWLEDGE_SOURCE_TYPE_LABELS[type]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="create-knowledge-title">Title</Label>
            <Input
              id="create-knowledge-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
            />
          </div>
          {sourceType === "pasted_text" ? (
            <div className="flex flex-col gap-2">
              <Label htmlFor="create-knowledge-content">Content</Label>
              <Textarea
                id="create-knowledge-content"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                required
              />
            </div>
          ) : null}
          {sourceType === "url" ? (
            <div className="flex flex-col gap-2">
              <Label htmlFor="create-knowledge-url">URL</Label>
              <Input
                id="create-knowledge-url"
                type="url"
                value={sourceUrl}
                onChange={(e) => setSourceUrl(e.target.value)}
                required
              />
            </div>
          ) : null}
          {sourceType === "file_upload" ? (
            <div className="flex flex-col gap-2">
              <Label htmlFor="create-knowledge-file">File</Label>
              <Input
                id="create-knowledge-file"
                type="file"
                accept=".txt,.md,text/plain,text/markdown"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </div>
          ) : null}
          <div className="flex flex-col gap-2">
            <Label htmlFor="create-knowledge-sensitivity">Sensitivity</Label>
            <Select
              value={sensitivity}
              onValueChange={(value) => setSensitivity(value as KnowledgeSensitivity)}
            >
              <SelectTrigger id="create-knowledge-sensitivity" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="normal">{KNOWLEDGE_SENSITIVITY_LABELS.normal}</SelectItem>
                <SelectItem value="private">{KNOWLEDGE_SENSITIVITY_LABELS.private}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
        <CardFooter className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? "Creating…" : "Create document"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
