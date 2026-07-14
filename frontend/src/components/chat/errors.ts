const ERROR_MESSAGES: Record<string, string> = {
  no_provider: "Şu anda yapılandırılmış bir yapay zeka sağlayıcısı yok. Lütfen daha sonra tekrar deneyin.",
  client_disconnect: "Bağlantı kesildi.",
};

export function mapErrorType(errorType: string): string {
  return ERROR_MESSAGES[errorType] ?? "Asistan beklenmedik bir hatayla karşılaştı. Lütfen tekrar deneyin.";
}
