/**
 * Shared frontend API error-sanitization and timeout helpers.
 */

export const MAX_SAFE_MESSAGE_LENGTH = 300
export const MAX_ERROR_CODE_LENGTH = 64

export function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function containsStackTraceOrHtml(message: string): boolean {
  const lower = message.toLowerCase()
  return (
    lower.includes('<!doctype') ||
    lower.includes('<html') ||
    lower.includes('traceback (most recent call last)') ||
    /\n\s+at\s+/.test(message) ||
    /\s+at\s+\S+\s+\([^)]+:\d+:\d+\)/.test(message)
  )
}

export function sanitizeApiMessage(
  rawMessage: unknown,
  fallback: string,
  sensitiveValues: readonly string[] = [],
): string {
  if (typeof rawMessage !== 'string') return fallback
  const normalizedWhitespace = rawMessage.replace(/\r\n/g, '\n').trim()
  if (!normalizedWhitespace || containsStackTraceOrHtml(normalizedWhitespace)) {
    return fallback
  }

  let sanitized = normalizedWhitespace.split('\n')[0]?.trim() ?? ''
  if (!sanitized) return fallback

  for (const secret of sensitiveValues) {
    if (secret.length >= 3 && sanitized.includes(secret)) {
      sanitized = sanitized.split(secret).join('[REDACTED]')
    }
  }

  sanitized = sanitized
    .replace(/:\/\/[^\s/]+@/g, '://[REDACTED]@')
    .replace(/\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi, '[REDACTED]')
    .replace(
      /\b(password|passwd|pwd|secret|token|api[_-]?key|x-api-key|bearerToken|authorization|connectionUri|dsn|conninfo)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;}\]]+)/gi,
      '$1=[REDACTED]',
    )
    .replace(
      /\b(?:10(?:\.\d{1,3}){3}|127(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b/g,
      '[internal-host]',
    )
    .replace(/\[::1\]/g, '[internal-host]')

  if (sanitized.length > MAX_SAFE_MESSAGE_LENGTH) {
    return `${sanitized.slice(0, MAX_SAFE_MESSAGE_LENGTH - 1)}…`
  }

  return sanitized
}

export function normalizeApiErrorCode(
  rawCode: unknown,
  fallback: string,
): string {
  if (typeof rawCode !== 'string') return fallback
  const trimmed = rawCode.trim()
  if (
    trimmed.length === 0 ||
    trimmed.length > MAX_ERROR_CODE_LENGTH ||
    !/^[A-Za-z0-9_.:-]+$/.test(trimmed)
  ) {
    return fallback
  }
  return trimmed
}

export function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === 'AbortError') ||
    (error instanceof Error && error.name === 'AbortError')
  )
}

export function resolveTimeoutMs(
  timeoutMs: number | undefined,
  defaultTimeoutMs: number,
): number {
  if (
    typeof timeoutMs === 'number' &&
    Number.isFinite(timeoutMs) &&
    timeoutMs > 0
  ) {
    return timeoutMs
  }
  return defaultTimeoutMs
}
