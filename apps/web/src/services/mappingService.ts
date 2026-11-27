import { buildApiUrl } from '../config/env.ts'
import type {
  CreateMappingPayload,
  ListMappingsParams,
  MappingListResponse,
  MappingRecord,
  MappingValidationResponse,
  UpdateMappingPayload,
} from '../types/mapping.ts'
import {
  isAbortError,
  isPlainRecord,
  normalizeApiErrorCode,
  resolveTimeoutMs,
  sanitizeApiMessage,
} from './apiClientUtils.ts'

export const DEFAULT_MAPPING_REQUEST_TIMEOUT_MS = 15_000

const MAPPINGS_ENDPOINT = '/api/v1/mappings'

export interface MappingRequestOptions {
  timeoutMs?: number
  signal?: AbortSignal
  fetcher?: typeof fetch
}

export class MappingApiClientError extends Error {
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
    this.name = 'MappingApiClientError'
    this.status = options.status
    this.errorCode = options.errorCode
    this.isAborted = Boolean(options.isAborted)
  }
}

function mapStatusToDefaultCode(httpStatus: number): string {
  if (httpStatus === 404) return 'MAPPING_NOT_FOUND'
  if (httpStatus === 409) return 'INVALID_MAPPING_REFERENCE'
  if (httpStatus === 400 || httpStatus === 422) return 'INVALID_MAPPING_RULE'
  if (httpStatus === 502) return 'NETWORK_ERROR'
  if (httpStatus === 503) return 'DATABASE_UNAVAILABLE'
  if (httpStatus === 408 || httpStatus === 504) return 'TIMEOUT'
  return 'SERVER_ERROR'
}

function fallbackMessageForError(httpStatus: number, errorCode: string): string {
  if (errorCode === 'MAPPING_NOT_FOUND' || httpStatus === 404) {
    return 'The requested mapping could not be found.'
  }
  if (errorCode === 'CONNECTION_NOT_FOUND') {
    return 'One of the referenced connections no longer exists.'
  }
  if (errorCode === 'MAPPING_VERSION_CONFLICT') {
    return 'This mapping was modified elsewhere. Reload the latest version before saving or validating.'
  }
  if (errorCode === 'DUPLICATE_MAPPING') {
    return 'A mapping with this name already exists.'
  }
  if (errorCode === 'DUPLICATE_DESTINATION_FIELD') {
    return 'Each destination field can only be targeted by one rule.'
  }
  if (errorCode === 'INVALID_MAPPING_REFERENCE') {
    return 'Mapping schema references are stale or invalid. Reload schemas and try again.'
  }
  if (errorCode === 'SCHEMA_NOT_DISCOVERED') {
    return 'Both source and destination connections must have discovered schemas first.'
  }
  if (errorCode === 'SOURCE_OBJECT_NOT_FOUND') {
    return 'The selected source object was not found in the source connection schema.'
  }
  if (errorCode === 'DESTINATION_OBJECT_NOT_FOUND') {
    return 'The selected destination object was not found in the destination connection schema.'
  }
  if (errorCode === 'DATABASE_UNAVAILABLE' || httpStatus === 503) {
    return 'Mapping persistence service is currently unavailable.'
  }
  if (errorCode === 'TIMEOUT' || httpStatus === 408 || httpStatus === 504) {
    return 'The mapping request timed out before the server responded.'
  }
  return 'Unable to complete the mapping request. Check your configuration and try again.'
}

async function parseErrorAndThrow(response: Response): Promise<never> {
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
    fallbackMessageForError(response.status, errorCode),
  )

  throw new MappingApiClientError({
    status: response.status,
    errorCode,
    message,
  })
}

async function requestMappingJson<T>(
  path: string,
  init: {
    method: 'GET' | 'POST' | 'PUT' | 'DELETE'
    bodyPayload?: unknown
  },
  options: MappingRequestOptions = {},
): Promise<T> {
  const fetcher = options.fetcher ?? globalThis.fetch
  if (typeof fetcher !== 'function') {
    throw new MappingApiClientError({
      status: 0,
      errorCode: 'NETWORK_ERROR',
      message: 'Unable to reach the backend API service.',
    })
  }

  if (options.signal?.aborted) {
    throw new MappingApiClientError({
      status: 0,
      errorCode: 'ABORTED',
      message: 'Request was cancelled.',
      isAborted: true,
    })
  }

  const timeoutMs = resolveTimeoutMs(
    options.timeoutMs,
    DEFAULT_MAPPING_REQUEST_TIMEOUT_MS,
  )
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
      await parseErrorAndThrow(response)
    }

    if (response.status === 204) {
      return undefined as T
    }

    try {
      return (await response.json()) as T
    } catch (jsonError) {
      if (timedOut || isAbortError(jsonError)) {
        throw jsonError
      }
      throw new MappingApiClientError({
        status: response.status,
        errorCode: 'SERVER_ERROR',
        message: 'Received an invalid JSON response from the backend API.',
      })
    }
  } catch (error) {
    if (error instanceof MappingApiClientError) {
      throw error
    }
    if (timedOut) {
      throw new MappingApiClientError({
        status: 504,
        errorCode: 'TIMEOUT',
        message: 'The mapping request timed out before the server responded.',
      })
    }
    if (isAbortError(error)) {
      throw new MappingApiClientError({
        status: 0,
        errorCode: 'ABORTED',
        message: 'Request was cancelled.',
        isAborted: true,
      })
    }
    throw new MappingApiClientError({
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

function assertValidMappingRecord(raw: unknown): MappingRecord {
  if (
    !isPlainRecord(raw) ||
    typeof raw.id !== 'string' ||
    typeof raw.name !== 'string' ||
    typeof raw.sourceConnectionId !== 'string' ||
    typeof raw.sourceObjectKey !== 'string' ||
    typeof raw.destinationConnectionId !== 'string' ||
    typeof raw.destinationObjectKey !== 'string' ||
    typeof raw.status !== 'string' ||
    typeof raw.mappingVersion !== 'number' ||
    !Array.isArray(raw.rules)
  ) {
    throw new MappingApiClientError({
      status: 502,
      errorCode: 'SERVER_ERROR',
      message: 'Received an unexpected mapping record format from the backend.',
    })
  }
  return raw as unknown as MappingRecord
}

export async function listMappings(
  params: ListMappingsParams = {},
  options: MappingRequestOptions = {},
): Promise<MappingListResponse> {
  const searchParams = new URLSearchParams()
  if (params.sourceConnectionId) {
    searchParams.set('sourceConnectionId', params.sourceConnectionId)
  }
  if (params.destinationConnectionId) {
    searchParams.set('destinationConnectionId', params.destinationConnectionId)
  }
  if (params.status) {
    searchParams.set('status', params.status)
  }
  if (params.q && params.q.trim()) {
    searchParams.set('q', params.q.trim())
  }
  if (params.limit !== undefined) {
    searchParams.set('limit', String(params.limit))
  }
  if (params.offset !== undefined) {
    searchParams.set('offset', String(params.offset))
  }

  const qs = searchParams.toString()
  const raw = await requestMappingJson<unknown>(
    qs ? `${MAPPINGS_ENDPOINT}?${qs}` : MAPPINGS_ENDPOINT,
    { method: 'GET' },
    options,
  )

  if (
    !isPlainRecord(raw) ||
    !Array.isArray(raw.items) ||
    typeof raw.total !== 'number'
  ) {
    throw new MappingApiClientError({
      status: 502,
      errorCode: 'SERVER_ERROR',
      message: 'Received an unexpected mapping list response from the backend.',
    })
  }

  return {
    items: raw.items.map(assertValidMappingRecord),
    total: raw.total,
    limit: typeof raw.limit === 'number' ? raw.limit : 50,
    offset: typeof raw.offset === 'number' ? raw.offset : 0,
  }
}

export async function getMapping(
  mappingId: string,
  options: MappingRequestOptions = {},
): Promise<MappingRecord> {
  const path = `${MAPPINGS_ENDPOINT}/${encodeURIComponent(mappingId.trim())}`
  const raw = await requestMappingJson<unknown>(path, { method: 'GET' }, options)
  return assertValidMappingRecord(raw)
}

export async function createMapping(
  payload: CreateMappingPayload,
  options: MappingRequestOptions = {},
): Promise<MappingRecord> {
  const raw = await requestMappingJson<unknown>(
    MAPPINGS_ENDPOINT,
    { method: 'POST', bodyPayload: payload },
    options,
  )
  return assertValidMappingRecord(raw)
}

export async function updateMapping(
  mappingId: string,
  payload: UpdateMappingPayload,
  options: MappingRequestOptions = {},
): Promise<MappingRecord> {
  const path = `${MAPPINGS_ENDPOINT}/${encodeURIComponent(mappingId.trim())}`
  const raw = await requestMappingJson<unknown>(
    path,
    { method: 'PUT', bodyPayload: payload },
    options,
  )
  return assertValidMappingRecord(raw)
}

export async function deleteMapping(
  mappingId: string,
  options: MappingRequestOptions = {},
): Promise<void> {
  const path = `${MAPPINGS_ENDPOINT}/${encodeURIComponent(mappingId.trim())}`
  await requestMappingJson<void>(path, { method: 'DELETE' }, options)
}

export async function validateMapping(
  mappingId: string,
  options: MappingRequestOptions = {},
): Promise<MappingValidationResponse> {
  const path = `${MAPPINGS_ENDPOINT}/${encodeURIComponent(mappingId.trim())}/validate`
  const raw = await requestMappingJson<unknown>(
    path,
    { method: 'POST' },
    options,
  )

  if (
    !isPlainRecord(raw) ||
    typeof raw.status !== 'string' ||
    !isPlainRecord(raw.validationSummary) ||
    typeof raw.validationSummary.isValid !== 'boolean' ||
    !Array.isArray(raw.validationSummary.issues)
  ) {
    throw new MappingApiClientError({
      status: 502,
      errorCode: 'SERVER_ERROR',
      message:
        'Received an unexpected mapping validation response from the backend.',
    })
  }

  return raw as unknown as MappingValidationResponse
}
