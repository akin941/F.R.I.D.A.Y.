const ERROR_MESSAGES: Record<string, string> = {
  no_provider: "No AI provider is configured right now. Please try again later.",
  client_disconnect: "The connection was interrupted.",
};

export function mapErrorType(errorType: string): string {
  return ERROR_MESSAGES[errorType] ?? "The assistant hit an unexpected error. Please try again.";
}
