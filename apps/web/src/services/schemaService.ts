import { buildApiUrl } from '../config/env.ts'
import type {
  DiscoverConnectionSchemaPayload,
  DiscoveredSchemaResponse,
  DiscoverSchemaResponse,
  ListSchemaFieldsParams,
  ListSchemaObjectsParams,
  SchemaObjectFieldsResponse,
  SchemaObjectListResponse,
} from '../types/schema.ts'
import {
  isAbortError,
  isPlainRecord,
  normalizeApiErrorCode,
  resolveTimeoutMs,
  sanitizeApiMessage,
} from './apiClientUtils.ts'

export const DEFAULT_SCHEMA_REQUEST_TIMEOUT_MS = 15_000
export const DEFAULT_SCHEMA_DISCOVERY_TIMEOUT_MS = 25_000

export interface SchemaRequestOptions {
  timeoutMs?: number
  signal?: AbortSignal
  fetcher?: typeof fetch
}

export class SchemaApiClientError extends Error {
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
    this.name = 'SchemaApiClientError'
    this.status = options.status
    this.errorCode = options.errorCode
    this.isAborted = Boolean(options.isAborted)
  }
}

function mapStatusToDefaultCode(httpStatus: number): string {
  if (httpStatus === 404) return 'SCHEMA_NOT_DISCOVERED'
  if (httpStatus === 409) return 'CONNECTION_IN_USE'
  if (httpStatus === 400 || httpStatus === 413 || httpStatus === 422) {
    return 'INVALID_CONFIGURATION'
  }
  if (httpStatus === 502) return 'NETWORK_ERROR'
  if (httpStatus === 503) return 'DATABASE_UNAVAILABLE'
  if (httpStatus === 408 || httpStatus === 504) return 'TIMEOUT'
  return 'SERVER_ERROR'
}

function fallbackMessageForStatus(httpStatus: number, errorCode: string): string {
  if (errorCode === 'CONNECTION_NOT_FOUND') {
    return 'The selected connection could not be found.'
  }
  if (errorCode === 'OBJECT_NOT_FOUND') {
    return 'The requested schema object could not be found for this connection.'
  }
  if (errorCode === 'SCHEMA_NOT_DISCOVERED' || httpStatus === 404) {
    return 'Schema has not been discovered for this connection yet.'
  }
  if (errorCode === 'FILE_REQUIRED_FOR_DISCOVERY') {
    return 'Select a valid file to discover schema metadata for this connector.'
  }
  if (errorCode === 'DISCOVERY_NOT_SUPPORTED') {
    return 'The selected discovery option is not supported for this connector type.'
  }
  if (errorCode === 'DATABASE_UNAVAILABLE' || httpStatus === 503) {
    return 'Schema discovery persistence service is currently unavailable.'
  }
  if (errorCode === 'TIMEOUT' || httpStatus === 408 || httpStatus === 504) {
    return 'Schema discovery timed out before the source responded.'
  }
  if (httpStatus === 413) {
    return 'Discovery payload exceeds the maximum allowed request size.'
  }
  if (
    errorCode === 'INVALID_CONFIGURATION' ||
    errorCode === 'AUTHENTICATION_FAILED' ||
    httpStatus === 422 ||
    httpStatus === 400
  ) {
    return 'Schema discovery could not complete with the current configuration.'
  }
  return 'Unable to complete the schema request. Please try again.'
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

  throw new SchemaApiClientError({
    status: response.status,
    errorCode,
    message,
  })
}

async function requestSchemaJson<T>(
  path: string,
  init: {
    method: 'GET' | 'POST'
    bodyPayload?: unknown
    defaultTimeoutMs: number
  },
  options: SchemaRequestOptions = {},
): Promise<T> {
  const fetcher = options.fetcher ?? globalThis.fetch
  if (typeof fetcher !== 'function') {
    throw new SchemaApiClientError({
      status: 0,
      errorCode: 'NETWORK_ERROR',
      message: 'Unable to reach the backend API service.',
    })
  }

  if (options.signal?.aborted) {
    throw new SchemaApiClientError({
      status: 0,
      errorCode: 'ABORTED',
      message: 'Request was cancelled.',
      isAborted: true,
    })
  }

  const timeoutMs = resolveTimeoutMs(options.timeoutMs, init.defaultTimeoutMs)
  const controller = new AbortController()
  let timedOut = false

  const timeoutId = globalThis.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  const onExternalAbort = () => controller.abort()
  options.signal?.addEventListener('abort', onExternalAbort, { once: true })

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
      await parseErrorAndThrow(response, [])
    }

    try {
      return (await response.json()) as T
    } catch (jsonError) {
      if (timedOut || isAbortError(jsonError)) {
        throw jsonError
      }
      throw new SchemaApiClientError({
        status: response.status,
        errorCode: 'SERVER_ERROR',
        message: 'Received an invalid JSON response from the backend API.',
      })
    }
  } catch (error) {
    if (error instanceof SchemaApiClientError) {
      throw error
    }
    if (timedOut) {
      throw new SchemaApiClientError({
        status: 504,
        errorCode: 'TIMEOUT',
        message: 'The schema request timed out before the server responded.',
      })
    }
    if (isAbortError(error)) {
      throw new SchemaApiClientError({
        status: 0,
        errorCode: 'ABORTED',
        message: 'Request was cancelled.',
        isAborted: true,
      })
    }
    throw new SchemaApiClientError({
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

export async function discoverConnectionSchema(
  connectionId: string,
  payload: DiscoverConnectionSchemaPayload = {},
  options: SchemaRequestOptions = {},
): Promise<DiscoverSchemaResponse> {
  const path = `/api/v1/connections/${encodeURIComponent(connectionId.trim())}/discover`
  const raw = await requestSchemaJson<unknown>(
    path,
    {
      method: 'POST',
      bodyPayload: payload,
      defaultTimeoutMs: DEFAULT_SCHEMA_DISCOVERY_TIMEOUT_MS,
    },
    options,
  )

  if (
    !isPlainRecord(raw) ||
    typeof raw.snapshotId !== 'string' ||
    typeof raw.connectionId !== 'string' ||
    typeof raw.discoveryStatus !== 'string' ||
    typeof raw.schemaFingerprint !== 'string' ||
    !Array.isArray(raw.objects)
  ) {
    throw new SchemaApiClientError({
      status: 502,
      errorCode: 'SERVER_ERROR',
      message: 'Received an unexpected discovery response format from the backend.',
    })
  }

  return raw as unknown as DiscoverSchemaResponse
}

export async function getConnectionSchema(
  connectionId: string,
  options: SchemaRequestOptions = {},
): Promise<DiscoveredSchemaResponse> {
  const path = `/api/v1/connections/${encodeURIComponent(connectionId.trim())}/schema`
  const raw = await requestSchemaJson<unknown>(
    path,
    {
      method: 'GET',
      defaultTimeoutMs: DEFAULT_SCHEMA_REQUEST_TIMEOUT_MS,
    },
    options,
  )

  if (
    !isPlainRecord(raw) ||
    typeof raw.id !== 'string' ||
    typeof raw.connectionId !== 'string' ||
    typeof raw.discoveryStatus !== 'string' ||
    typeof raw.schemaFingerprint !== 'string' ||
    !Array.isArray(raw.objects)
  ) {
    throw new SchemaApiClientError({
      status: 502,
      errorCode: 'SERVER_ERROR',
      message: 'Received an unexpected schema snapshot format from the backend.',
    })
  }

  return raw as unknown as DiscoveredSchemaResponse
}

export async function listSchemaObjects(
  connectionId: string,
  params: ListSchemaObjectsParams = {},
  options: SchemaRequestOptions = {},
): Promise<SchemaObjectListResponse> {
  const searchParams = new URLSearchParams()
  if (params.q && params.q.trim()) searchParams.set('q', params.q.trim())
  if (params.objectType) searchParams.set('objectType', params.objectType)
  if (params.objectKey && params.objectKey.trim()) {
    searchParams.set('objectKey', params.objectKey.trim())
  }
  if (params.limit !== undefined) searchParams.set('limit', String(params.limit))
  if (params.offset !== undefined) {
    searchParams.set('offset', String(params.offset))
  }

  const basePath = `/api/v1/connections/${encodeURIComponent(connectionId.trim())}/objects`
  const qs = searchParams.toString()
  const raw = await requestSchemaJson<unknown>(
    qs ? `${basePath}?${qs}` : basePath,
    {
      method: 'GET',
      defaultTimeoutMs: DEFAULT_SCHEMA_REQUEST_TIMEOUT_MS,
    },
    options,
  )

  if (
    !isPlainRecord(raw) ||
    !Array.isArray(raw.items) ||
    typeof raw.total !== 'number'
  ) {
    throw new SchemaApiClientError({
      status: 502,
      errorCode: 'SERVER_ERROR',
      message: 'Received an unexpected schema objects list from the backend.',
    })
  }

  return raw as unknown as SchemaObjectListResponse
}

export async function getSchemaObjectFields(
  connectionId: string,
  objectId: string,
  params: ListSchemaFieldsParams = {},
  options: SchemaRequestOptions = {},
): Promise<SchemaObjectFieldsResponse> {
  const searchParams = new URLSearchParams()
  if (params.q && params.q.trim()) searchParams.set('q', params.q.trim())
  if (params.limit !== undefined) searchParams.set('limit', String(params.limit))
  if (params.offset !== undefined) {
    searchParams.set('offset', String(params.offset))
  }

  const basePath = `/api/v1/connections/${encodeURIComponent(connectionId.trim())}/objects/${encodeURIComponent(objectId.trim())}/fields`
  const qs = searchParams.toString()
  const raw = await requestSchemaJson<unknown>(
    qs ? `${basePath}?${qs}` : basePath,
    {
      method: 'GET',
      defaultTimeoutMs: DEFAULT_SCHEMA_REQUEST_TIMEOUT_MS,
    },
    options,
  )

  if (
    !isPlainRecord(raw) ||
    !isPlainRecord(raw.object) ||
    !Array.isArray(raw.fields) ||
    typeof raw.total !== 'number'
  ) {
    throw new SchemaApiClientError({
      status: 502,
      errorCode: 'SERVER_ERROR',
      message: 'Received an unexpected object fields response from the backend.',
    })
  }

  return raw as unknown as SchemaObjectFieldsResponse
}
