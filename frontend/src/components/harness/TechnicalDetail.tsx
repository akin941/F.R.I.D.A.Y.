/**
 * A collapsed "Teknik ayrıntı" disclosure for raw technical text (e.g. a
 * push error from git). Shown as preformatted plain text, never as prose
 * mixed into the UI's own sentences, and hidden until asked for.
 */
export function TechnicalDetail({ text }: { text: string }) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted-foreground">Teknik ayrıntı</summary>
      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted p-2">
        {text}
      </pre>
    </details>
  );
}
