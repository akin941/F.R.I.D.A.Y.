const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Validates a dynamic route segment as a UUID before it is interpolated into
 * an upstream backend path. Next.js decodes route params and `fetch`
 * normalizes dot segments/query delimiters, so an unvalidated segment (e.g.
 * `../ai/conversations?ignored=`) could redirect a bearer-authenticated
 * server-side request to an unintended backend path.
 */
export function isValidUuid(value: string): boolean {
  return UUID_PATTERN.test(value);
}
