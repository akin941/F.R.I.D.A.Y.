import { tr } from "@/lib/i18n/tr";

/** Turkish text for an SSE `error` event's `error_type`. */
export function mapErrorType(errorType: string): string {
  return tr.chat.streamErrors[errorType] ?? tr.chat.streamFailed;
}
