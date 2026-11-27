import { buildApiUrl } from '../config/env'
import type {
  ConnectionTestErrorCode,
  ConnectionTestRequest,
  ConnectionTestResult,
  ConnectionTestTerminalStatus,
  KnownConnectionTestErrorCode,
} from '../types/connectionTest'

export const CONNECTION_TEST_ENDPOINT = '/api/v1/connections/test'
export const DEFAULT_CONNECTION_TEST_TIMEOUT_MS = 15_000

const MAX_MESSAGE_LENGTH = 300
const MAX_ERROR_CODE_LENGTH = 64

const SAFE_ERROR_MESSAGES: Record<KnownConnectionTestErrorCode, string> = {
  CONNECTION_REFUSED:
    'Unable to reach the target endpoint. Connection was refused.',
  DNS_RESOLUTION_FAILED:
    'Unable to resolve the target hostname. Verify the host address and try again.',
  AUTHENTICATION_FAILED:
    'Authentication failed. Verify your credentials and try again.',
  TIMEOUT: 'Connection test timed out before the server responded.',
  INVALID_CONFIGURATION:
    'The connection configuration is invalid or incomplete.',
  UNSUPPORTED_CONNECTOR:
    'This connector type is not supported by the connection test service.',
  TESTER_NOT_IMPLEMENTED:
    'Connection testing for this connector type is not implemented yet.',
  SERVER_ERROR:
    'The connection test service encountered an unexpected error. Please try again.',
  NETWORK_ERROR:
    'Unable to reach the connection test service. Check your network connection and try again.',
}

export interface TestConnectionOptions {
  /** Request timeout in milliseconds. Defaults to 15,000 ms. */
  timeoutMs?: number
  /** Optional external AbortSignal for caller-initiated cancellation. */
  signal?: AbortSignal
  /** Optional fetch implementation override (for testing/injection). */
  fetcher?: typeof fetch
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function resolveTimeoutMs(timeoutMs: number | undefined): number {
  if (
    typeof timeoutMs === 'number' &&
    Number.isFinite(timeoutMs) &&
    timeoutMs > 0
  ) {
    return timeoutMs
  }
  return DEFAULT_CONNECTION_TEST_TIMEOUT_MS
}

function measureDurationMs(startedAt: number): number {
  return Math.max(0, Math.round(Date.now() - startedAt))
}

function normalizeDurationMs(rawValue: unknown, fallbackMs: number): number {
  if (
    typeof rawValue === 'number' &&
    Number.isFinite(rawValue) &&
    rawValue >= 0
  ) {
    return Math.round(rawValue)
  }
  return fallbackMs
}

function normalizeTestedAt(rawValue: unknown): string {
  if (typeof rawValue === 'string' && rawValue.trim().length > 0) {
    const parsed = Date.parse(rawValue)
    if (!Number.isNaN(parsed)) {
      return rawValue.trim()
    }
  }
  return new Date().toISOString()
}

function mapHttpStatusToErrorCode(status: number): KnownConnectionTestErrorCode {
  if (status === 401 || status === 403) return 'AUTHENTICATION_FAILED'
  if (status === 408 || status === 504) return 'TIMEOUT'
  if (status === 400 || status === 422) return 'INVALID_CONFIGURATION'
  if (status === 501) return 'UNSUPPORTED_CONNECTOR'
  return 'SERVER_ERROR'
}

function normalizeErrorCode(
  rawCode: unknown,
  fallback: KnownConnectionTestErrorCode,
): ConnectionTestErrorCode {
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

function collectSensitiveValues(request: ConnectionTestRequest): string[] {
  const candidates: Array<string | undefined> = []

  switch (request.connectorType) {
    case 'PostgreSQL':
    case 'MySQL':
      candidates.push(request.credentials?.password)
      break
    case 'MongoDB':
      candidates.push(
        request.credentials?.password,
        request.credentials?.connectionUri,
      )
      break
    case 'REST API':
      candidates.push(
        request.credentials?.apiKey,
        request.credentials?.bearerToken,
      )
      break
    case 'CSV':
    case 'JSON':
      break
  }

  return candidates.filter(
    (value): value is string =>
      typeof value === 'string' && value.trim().length >= 3,
  )
}

function containsStackTraceOrHtml(message: string): boolean {
  const lower = message.toLowerCase()
  return (
    lower.includes('<!doctype') ||
    lower.includes('<html') ||
    lower.includes('traceback (most recent call last)') ||
    /\n\s+at\s+/.test(message) ||
    /\s+at\s+\S+\s+\([^)]+:\d+:\d+\)/.test(message)
  )
}

function sanitizeMessage(
  rawMessage: unknown,
  fallback: string,
  sensitiveValues: readonly string[],
): string {
  if (typeof rawMessage !== 'string') return fallback
  const normalizedWhitespace = rawMessage.replace(/\r\n/g, '\n').trim()
  if (!normalizedWhitespace || containsStackTraceOrHtml(normalizedWhitespace)) {
    return fallback
  }

  let sanitized = normalizedWhitespace.split('\n')[0]?.trim() ?? ''
  if (!sanitized) return fallback

  for (const secret of sensitiveValues) {
    if (sanitized.includes(secret)) {
      sanitized = sanitized.split(secret).join('[REDACTED]')
    }
  }

  // Strip embedded URI credentials, bearer tokens, key=value secrets, and private IPs.
  sanitized = sanitized
    .replace(/:\/\/[^/@\s]+@/g, '://[REDACTED]@')
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer [REDACTED]')
    .replace(
      /\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+/gi,
      '$1=[REDACTED]',
    )
    .replace(
      /\b(?:10(?:\.\d{1,3}){3}|127(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b/g,
      '[internal-host]',
    )
    .replace(/\[::1\]/g, '[internal-host]')

  if (sanitized.length > MAX_MESSAGE_LENGTH) {
    return `${sanitized.slice(0, MAX_MESSAGE_LENGTH - 1)}…`
  }

  return sanitized
}

function getFallbackFailureMessage(code: ConnectionTestErrorCode): string {
  if (code in SAFE_ERROR_MESSAGES) {
    return SAFE_ERROR_MESSAGES[code as KnownConnectionTestErrorCode]
  }
  return 'Unable to complete the connection test. Please verify your settings and try again.'
}

function buildSerializedRequestBody(request: ConnectionTestRequest): string {
  const base: Record<string, unknown> = {
    connectorType: request.connectorType,
    config: request.config,
  }

  if (request.connectionId && request.connectionId.trim().length > 0) {
    base.connectionId = request.connectionId.trim()
  }

  if (request.connectorType === 'CSV' || request.connectorType === 'JSON') {
    base.fileContent = request.fileContent
  }

  if (request.credentials) {
    const cleanedEntries = Object.entries(request.credentials).filter(
      ([, value]) => typeof value === 'string' && value.length > 0,
    )
    if (cleanedEntries.length > 0) {
      base.credentials = Object.fromEntries(cleanedEntries)
    }
  }

  return JSON.stringify(base)
}

function normalizeFailurePayload(
  raw: Record<string, unknown> | null,
  fallbackErrorCode: KnownConnectionTestErrorCode,
  fallbackDurationMs: number,
  sensitiveValues: readonly string[],
): ConnectionTestResult {
  const errorCode = normalizeErrorCode(
    raw?.errorCode ?? raw?.code,
    fallbackErrorCode,
  )
  const status: ConnectionTestTerminalStatus =
    raw?.status === 'timeout' || errorCode === 'TIMEOUT' ? 'timeout' : 'failed'

  const fallbackMessage = getFallbackFailureMessage(errorCode)
  const message = sanitizeMessage(
    raw?.message ?? raw?.error,
    fallbackMessage,
    sensitiveValues,
  )

  return {
    success: false,
    status,
    message,
    errorCode,
    durationMs: normalizeDurationMs(raw?.durationMs, fallbackDurationMs),
    testedAt: normalizeTestedAt(raw?.testedAt),
  }
}

function normalizeBackendResponse(
  raw: unknown,
  httpOk: boolean,
  httpStatus: number,
  fallbackDurationMs: number,
  sensitiveValues: readonly string[],
): ConnectionTestResult {
  if (!isPlainRecord(raw)) {
    return {
      success: false,
      status: httpStatus === 408 || httpStatus === 504 ? 'timeout' : 'failed',
      message: httpOk
        ? 'Received an unexpected response format from the connection test service.'
        : getFallbackFailureMessage(mapHttpStatusToErrorCode(httpStatus)),
      errorCode: httpOk ? 'SERVER_ERROR' : mapHttpStatusToErrorCode(httpStatus),
      durationMs: fallbackDurationMs,
      testedAt: new Date().toISOString(),
    }
  }

  if (!httpOk) {
    return normalizeFailurePayload(
      raw,
      mapHttpStatusToErrorCode(httpStatus),
      fallbackDurationMs,
      sensitiveValues,
    )
  }

  if (raw.success === true && (raw.status === undefined || raw.status === 'connected')) {
    return {
      success: true,
      status: 'connected',
      message: sanitizeMessage(
        raw.message,
        'Connection established successfully.',
        sensitiveValues,
      ),
      durationMs: normalizeDurationMs(raw.durationMs, fallbackDurationMs),
      testedAt: normalizeTestedAt(raw.testedAt),
    }
  }

  if (
    raw.success === false &&
    (raw.status === undefined ||
      raw.status === 'failed' ||
      raw.status === 'timeout')
  ) {
    return normalizeFailurePayload(
      raw,
      'SERVER_ERROR',
      fallbackDurationMs,
      sensitiveValues,
    )
  }

  // Unexpected or contradictory payload shape on 2xx: never treat as a fake success.
  return {
    success: false,
    status: 'failed',
    message:
      'Received an unexpected response from the connection test service.',
    errorCode: 'SERVER_ERROR',
    durationMs: normalizeDurationMs(raw.durationMs, fallbackDurationMs),
    testedAt: normalizeTestedAt(raw.testedAt),
  }
}

function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === 'AbortError') ||
    (error instanceof Error && error.name === 'AbortError')
  )
}

/**
 * Executes a backend-authoritative connection test via POST /api/v1/connections/test.
 *
 * Security & Credential Limitation:
 * - Existing saved connections do not store passwords, API keys, bearer tokens, or
 *   MongoDB URI credentials in the frontend.
 * - Transient credentials may be supplied in `request.credentials` and are transmitted
 *   strictly in the HTTPS/HTTP request body; they are never persisted, fabricated, or logged.
 * - All responses and errors are normalized and sanitized so secrets, internal hosts,
 *   and backend stack traces are never exposed to the caller.
 */
export async function testConnection(
  request: ConnectionTestRequest,
  options: TestConnectionOptions = {},
): Promise<ConnectionTestResult> {
  const startedAt = Date.now()
  const sensitiveValues = collectSensitiveValues(request)
  const fetcher = options.fetcher ?? globalThis.fetch

  if (typeof fetcher !== 'function') {
    return {
      success: false,
      status: 'failed',
      message: SAFE_ERROR_MESSAGES.NETWORK_ERROR,
      errorCode: 'NETWORK_ERROR',
      durationMs: measureDurationMs(startedAt),
      testedAt: new Date().toISOString(),
    }
  }

  if (options.signal?.aborted) {
    return {
      success: false,
      status: 'failed',
      message: 'Connection test was cancelled before completion.',
      errorCode: 'NETWORK_ERROR',
      durationMs: measureDurationMs(startedAt),
      testedAt: new Date().toISOString(),
    }
  }

  const timeoutMs = resolveTimeoutMs(options.timeoutMs)
  const controller = new AbortController()
  let timedOut = false

  const timeoutId = globalThis.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  const handleExternalAbort = () => {
    controller.abort()
  }

  options.signal?.addEventListener('abort', handleExternalAbort, { once: true })

  try {
    const response = await fetcher(buildApiUrl(CONNECTION_TEST_ENDPOINT), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: buildSerializedRequestBody(request),
      signal: controller.signal,
    })

    const elapsedMs = measureDurationMs(startedAt)

    let parsedBody: unknown = null
    let jsonParseFailed = false

    try {
      parsedBody = await response.json()
    } catch {
      jsonParseFailed = true
    }

    if (jsonParseFailed) {
      const errorCode = response.ok
        ? 'SERVER_ERROR'
        : mapHttpStatusToErrorCode(response.status)
      return {
        success: false,
        status: errorCode === 'TIMEOUT' ? 'timeout' : 'failed',
        message: response.ok
          ? 'Received a malformed response from the connection test service.'
          : getFallbackFailureMessage(errorCode),
        errorCode,
        durationMs: elapsedMs,
        testedAt: new Date().toISOString(),
      }
    }

    return normalizeBackendResponse(
      parsedBody,
      response.ok,
      response.status,
      elapsedMs,
      sensitiveValues,
    )
  } catch (error) {
    const elapsedMs = measureDurationMs(startedAt)

    if (timedOut) {
      return {
        success: false,
        status: 'timeout',
        message: SAFE_ERROR_MESSAGES.TIMEOUT,
        errorCode: 'TIMEOUT',
        durationMs: elapsedMs,
        testedAt: new Date().toISOString(),
      }
    }

    if (isAbortError(error)) {
      return {
        success: false,
        status: 'failed',
        message: 'Connection test was cancelled before completion.',
        errorCode: 'NETWORK_ERROR',
        durationMs: elapsedMs,
        testedAt: new Date().toISOString(),
      }
    }

    return {
      success: false,
      status: 'failed',
      message: SAFE_ERROR_MESSAGES.NETWORK_ERROR,
      errorCode: 'NETWORK_ERROR',
      durationMs: elapsedMs,
      testedAt: new Date().toISOString(),
    }
  } finally {
    globalThis.clearTimeout(timeoutId)
    options.signal?.removeEventListener('abort', handleExternalAbort)
  }
}
