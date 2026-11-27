import { buildApiUrl } from '../config/env.ts'
import type {
  Connection,
  ConnectionListResponse,
  CreateConnectionPayload,
  ListConnectionsParams,
  UpdateConnectionPayload,
} from '../types/connection.ts'
import {
  isAbortError,
  isPlainRecord,
  normalizeApiErrorCode,
  resolveTimeoutMs,
  sanitizeApiMessage,
} from './apiClientUtils.ts'

export const CONNECTIONS_ENDPOINT = '/api/v1/connections'
export const DEFAULT_CONNECTIONS_TIMEOUT_MS = 15_000

export interface ConnectionRequestOptions {
  timeoutMs?: number
  signal?: AbortSignal
  fetcher?: typeof fetch
}

export class ConnectionApiClientError extends Error {
  readonly status: number
  readonly errorCode: string
  readonly isAborted: boolean

  constructor(options: {
    status: number
    errorCode: string
    message: string
    isAborted?: boolean
  }) {
    super(options.message)
    this.name = 'ConnectionApiClientError'
    this.status = options.status
    this.errorCode = options.errorCode
    this.isAborted = Boolean(options.isAborted)
  }
}

function mapStatusToDefaultCode(httpStatus: number): string {
  if (httpStatus === 404) return 'CONNECTION_NOT_FOUND'
  if (httpStatus === 409) return 'DUPLICATE_CONNECTION'
  if (httpStatus === 400 || httpStatus === 413 || httpStatus === 422) {
    return 'INVALID_CONFIGURATION'
  }
  if (httpStatus === 503) return 'DATABASE_UNAVAILABLE'
  if (httpStatus === 408 || httpStatus === 504) return 'TIMEOUT'
  return 'SERVER_ERROR'
}

function fallbackMessageForStatus(httpStatus: number, errorCode: string): string {
  if (errorCode === 'CONNECTION_NOT_FOUND' || httpStatus === 404) {
    return 'The requested connection could not be found.'
  }
  if (errorCode === 'DUPLICATE_CONNECTION' || httpStatus === 409) {
    return 'A connection with this name already exists.'
  }
  if (errorCode === 'DATABASE_UNAVAILABLE' || httpStatus === 503) {
    return 'Connection persistence service is currently unavailable.'
  }
  if (errorCode === 'INVALID_CONFIGURATION' || httpStatus === 422 || httpStatus === 400) {
    return 'The connection configuration or credentials are invalid.'
  }
  if (errorCode === 'TIMEOUT' || httpStatus === 408 || httpStatus === 504) {
    return 'The request timed out before the server responded.'
  }
  return 'Unable to complete the connection request. Please try again.'
}

function extractSecretsFromPayload(payload: unknown): string[] {
  if (!isPlainRecord(payload) || !isPlainRecord(payload.credentials)) {
    return []
  }
  return Object.values(payload.credentials).filter(
    (value): value is string =>
      typeof value === 'string' && value.trim().length >= 3,
  )
}

function assertValidConnectionObject(raw: unknown): Connection {
  if (
    !isPlainRecord(raw) ||
    typeof raw.id !== 'string' ||
    typeof raw.name !== 'string' ||
    typeof raw.type !== 'string' ||
    typeof raw.status !== 'string' ||
    typeof raw.endpoint !== 'string'
  ) {
    throw new ConnectionApiClientError({
      status: 502,
      errorCode: 'SERVER_ERROR',
      message: 'Received an unexpected connection response from the backend.',
    })
  }
  return raw as unknown as Connection
}

async function parseErrorAndThrow(
  response: Response,
  sensitiveValues: readonly string[],
): Promise<never> {
  const defaultCode = mapStatusToDefaultCode(response.status)
  let rawBody: unknown
  try {
    rawBody = await response.json()
  } catch {
    rawBody = null
  }

  const record = isPlainRecord(rawBody) ? rawBody : null
  const errorCode = normalizeApiErrorCode(
    record?.errorCode ?? record?.code,
    defaultCode,
  )
  const message = sanitizeApiMessage(
    record?.message ?? record?.error,
    fallbackMessageForStatus(response.status, errorCode),
    sensitiveValues,
  )

  throw new ConnectionApiClientError({
    status: response.status,
    errorCode,
    message,
  })
}

async function requestJson<T>(
  path: string,
  init: {
    method: 'GET' | 'POST' | 'PUT' | 'DELETE'
    bodyPayload?: unknown
  },
  options: ConnectionRequestOptions = {},
): Promise<T> {
  const fetcher = options.fetcher ?? globalThis.fetch
  if (typeof fetcher !== 'function') {
    throw new ConnectionApiClientError({
      status: 0,
      errorCode: 'NETWORK_ERROR',
      message: 'Unable to reach the backend API service.',
    })
  }

  if (options.signal?.aborted) {
    throw new ConnectionApiClientError({
      status: 0,
      errorCode: 'ABORTED',
      message: 'Request was cancelled.',
      isAborted: true,
    })
  }

  const timeoutMs = resolveTimeoutMs(
    options.timeoutMs,
    DEFAULT_CONNECTIONS_TIMEOUT_MS,
  )
  const controller = new AbortController()
  let timedOut = false

  const timeoutId = globalThis.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  const onExternalAbort = () => controller.abort()
  options.signal?.addEventListener('abort', onExternalAbort, { once: true })

  const sensitiveValues = extractSecretsFromPayload(init.bodyPayload)

  try {
    const headers: Record<string, string> = {
      Accept: 'application/json',
    }
    const fetchInit: RequestInit = {
      method: init.method,
      headers,
      signal: controller.signal,
    }

    if (init.bodyPayload !== undefined) {
      headers['Content-Type'] = 'application/json'
      fetchInit.body = JSON.stringify(init.bodyPayload)
    }

    const response = await fetcher(buildApiUrl(path), fetchInit)
    if (!response.ok) {
      await parseErrorAndThrow(response, sensitiveValues)
    }

    if (response.status === 204) {
      return undefined as T
    }

    try {
      return (await response.json()) as T
    } catch {
      throw new ConnectionApiClientError({
        status: response.status,
        errorCode: 'SERVER_ERROR',
        message: 'Received an invalid JSON response from the backend API.',
      })
    }
  } catch (error) {
    if (error instanceof ConnectionApiClientError) {
      throw error
    }
    if (timedOut) {
      throw new ConnectionApiClientError({
        status: 504,
        errorCode: 'TIMEOUT',
        message: 'The request timed out before the server responded.',
      })
    }
    if (isAbortError(error)) {
      throw new ConnectionApiClientError({
        status: 0,
        errorCode: 'ABORTED',
        message: 'Request was cancelled.',
        isAborted: true,
      })
    }
    throw new ConnectionApiClientError({
      status: 0,
      errorCode: 'NETWORK_ERROR',
      message:
        'Unable to reach the backend API. Check that the server is running and try again.',
    })
  } finally {
    globalThis.clearTimeout(timeoutId)
    options.signal?.removeEventListener('abort', onExternalAbort)
  }
}

export async function listConnections(
  params: ListConnectionsParams = {},
  options: ConnectionRequestOptions = {},
): Promise<ConnectionListResponse> {
  const searchParams = new URLSearchParams()
  if (params.status) searchParams.set('status', params.status)
  if (params.type) searchParams.set('type', params.type)
  if (params.q && params.q.trim()) searchParams.set('q', params.q.trim())
  if (params.limit !== undefined) searchParams.set('limit', String(params.limit))
  if (params.offset !== undefined) {
    searchParams.set('offset', String(params.offset))
  }

  const queryString = searchParams.toString()
  const path = queryString
    ? `${CONNECTIONS_ENDPOINT}?${queryString}`
    : CONNECTIONS_ENDPOINT

  const raw = await requestJson<unknown>(path, { method: 'GET' }, options)
  if (
    !isPlainRecord(raw) ||
    !Array.isArray(raw.items) ||
    typeof raw.total !== 'number'
  ) {
    throw new ConnectionApiClientError({
      status: 502,
      errorCode: 'SERVER_ERROR',
      message: 'Received an unexpected connections list format from the backend.',
    })
  }

  return {
    items: raw.items.map(assertValidConnectionObject),
    total: raw.total,
    limit: typeof raw.limit === 'number' ? raw.limit : raw.items.length || 50,
    offset: typeof raw.offset === 'number' ? raw.offset : 0,
  }
}

export async function getConnection(
  connectionId: string,
  options: ConnectionRequestOptions = {},
): Promise<Connection> {
  const path = `${CONNECTIONS_ENDPOINT}/${encodeURIComponent(connectionId.trim())}`
  const raw = await requestJson<unknown>(path, { method: 'GET' }, options)
  return assertValidConnectionObject(raw)
}

export async function createConnection(
  payload: CreateConnectionPayload,
  options: ConnectionRequestOptions = {},
): Promise<Connection> {
  const raw = await requestJson<unknown>(
    CONNECTIONS_ENDPOINT,
    { method: 'POST', bodyPayload: payload },
    options,
  )
  return assertValidConnectionObject(raw)
}

export async function updateConnection(
  connectionId: string,
  payload: UpdateConnectionPayload,
  options: ConnectionRequestOptions = {},
): Promise<Connection> {
  const path = `${CONNECTIONS_ENDPOINT}/${encodeURIComponent(connectionId.trim())}`
  const raw = await requestJson<unknown>(
    path,
    { method: 'PUT', bodyPayload: payload },
    options,
  )
  return assertValidConnectionObject(raw)
}

export async function deleteConnection(
  connectionId: string,
  options: ConnectionRequestOptions = {},
): Promise<void> {
  const path = `${CONNECTIONS_ENDPOINT}/${encodeURIComponent(connectionId.trim())}`
  await requestJson<void>(path, { method: 'DELETE' }, options)
}
