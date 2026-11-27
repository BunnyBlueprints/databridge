import type { Connection, ConnectionAuthType } from '../../types/connection'
import type { ConnectionTestRequest } from '../../types/connectionTest'
import {
  extractFileName,
  getDefaultPort,
  parseMongoEndpoint,
  parseSqlEndpoint,
  sanitizeDisplayEndpoint,
} from './connectorForm'

/**
 * Mirrors backend CONNECTION_TEST_MAX_FILE_BYTES (256 KB) for early UI feedback.
 * Backend validation remains authoritative.
 */
export const MAX_TEST_FILE_BYTES = 262_144

export interface ConnectionTestFormValues {
  // PostgreSQL / MySQL
  host: string
  port: string
  database: string
  username: string
  password: string
  // MongoDB
  mongoUri: string
  mongoDatabase: string
  mongoUsername: string
  mongoPassword: string
  mongoConnectionUri: string
  // REST API
  baseUrl: string
  authType: ConnectionAuthType
  apiKey: string
  bearerToken: string
  // CSV / JSON (filename metadata only; file content is never stored here)
  fileName: string
}

export type ConnectionTestFormErrors = Partial<
  Record<keyof ConnectionTestFormValues | 'file', string>
>

export function createInitialTestValues(
  connection: Connection,
): ConnectionTestFormValues {
  const d = connection.details
  const values: ConnectionTestFormValues = {
    host: '',
    port: getDefaultPort(connection.type),
    database: '',
    username: '',
    password: '',
    mongoUri: '',
    mongoDatabase: '',
    mongoUsername: '',
    mongoPassword: '',
    mongoConnectionUri: '',
    baseUrl: '',
    authType: 'none',
    apiKey: '',
    bearerToken: '',
    fileName: '',
  }

  switch (connection.type) {
    case 'PostgreSQL':
    case 'MySQL': {
      const parsed = parseSqlEndpoint(connection.endpoint)
      values.host = d?.host ?? parsed.host ?? ''
      values.port =
        d?.port !== undefined
          ? String(d.port)
          : (parsed.port ?? getDefaultPort(connection.type))
      values.database = d?.database ?? parsed.database ?? ''
      values.username = ''
      break
    }

    case 'MongoDB': {
      const parsed = parseMongoEndpoint(connection.endpoint)
      const database = d?.database ?? parsed.database ?? ''
      const rawUri = sanitizeDisplayEndpoint(
        d?.sanitizedUri ?? parsed.sanitizedUri,
      )
      const withoutDb =
        database && rawUri.endsWith(`/${database}`)
          ? rawUri.slice(0, -(`/${database}`.length))
          : rawUri
      values.mongoUri =
        withoutDb &&
        !withoutDb.startsWith('mongodb://') &&
        !withoutDb.startsWith('mongodb+srv://')
          ? `mongodb+srv://${withoutDb}`
          : withoutDb
      values.mongoDatabase = database
      values.mongoUsername = ''
      break
    }

    case 'REST API': {
      values.baseUrl = sanitizeDisplayEndpoint(
        d?.baseUrl ?? connection.endpoint,
      )
      values.authType = d?.authType ?? 'none'
      break
    }

    case 'CSV':
    case 'JSON': {
      values.fileName =
        d?.fileName ?? extractFileName(connection.endpoint) ?? ''
      break
    }
  }

  return values
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  return `${kb >= 100 ? kb.toFixed(0) : kb.toFixed(1)} KB`
}

export function validateSelectedTestFile(
  file: File | null,
  connectorType: 'CSV' | 'JSON',
): string | null {
  if (!file) {
    return `Select a ${connectorType === 'CSV' ? '.csv' : '.json'} file to test.`
  }

  const expectedExt = connectorType === 'CSV' ? '.csv' : '.json'
  if (!file.name.toLowerCase().endsWith(expectedExt)) {
    return `Selected file must have a ${expectedExt} extension.`
  }

  if (file.size === 0) {
    return 'Selected file is empty.'
  }

  if (file.size > MAX_TEST_FILE_BYTES) {
    return `File size (${formatFileSize(file.size)}) exceeds the 256 KB (${MAX_TEST_FILE_BYTES.toLocaleString()} bytes) limit.`
  }

  return null
}

function isValidHttpUrl(raw: string): boolean {
  try {
    const parsed = new URL(raw)
    return (
      (parsed.protocol === 'http:' || parsed.protocol === 'https:') &&
      !parsed.username &&
      !parsed.password
    )
  } catch {
    return false
  }
}

function isValidMongoUri(raw: string): boolean {
  const trimmed = raw.trim()
  return (
    (trimmed.startsWith('mongodb://') ||
      trimmed.startsWith('mongodb+srv://')) &&
    trimmed.length > 'mongodb://'.length
  )
}

function hasEmbeddedUriCredentials(raw: string): boolean {
  return /:\/\/[^/@\s]+@/.test(raw.trim())
}

export function validateConnectionTestInputs(
  connection: Connection,
  values: ConnectionTestFormValues,
  selectedFile: File | null,
): ConnectionTestFormErrors {
  const errors: ConnectionTestFormErrors = {}

  switch (connection.type) {
    case 'PostgreSQL':
    case 'MySQL': {
      if (!values.host.trim()) {
        errors.host = 'Host is required.'
      }
      const trimmedPort = values.port.trim()
      if (!trimmedPort || !/^\d+$/.test(trimmedPort)) {
        errors.port = 'Port must be a number between 1 and 65535.'
      } else {
        const portNum = Number(trimmedPort)
        if (portNum < 1 || portNum > 65535) {
          errors.port = 'Port must be between 1 and 65535.'
        }
      }
      if (!values.database.trim()) {
        errors.database = 'Database name is required.'
      }
      if (!values.username.trim()) {
        errors.username = 'Username is required.'
      }
      break
    }

    case 'MongoDB': {
      if (!values.mongoUri.trim()) {
        errors.mongoUri = 'Connection URI is required.'
      } else if (!isValidMongoUri(values.mongoUri)) {
        errors.mongoUri =
          'URI must start with mongodb:// or mongodb+srv://.'
      } else if (hasEmbeddedUriCredentials(values.mongoUri)) {
        errors.mongoUri =
          'Remove embedded credentials from Connection URI and enter them in the transient credential fields below.'
      }

      if (!values.mongoDatabase.trim()) {
        errors.mongoDatabase = 'Database name is required.'
      }

      if (
        values.mongoConnectionUri.trim() &&
        !isValidMongoUri(values.mongoConnectionUri)
      ) {
        errors.mongoConnectionUri =
          'Override URI must start with mongodb:// or mongodb+srv://.'
      }
      break
    }

    case 'REST API': {
      const trimmedUrl = values.baseUrl.trim()
      if (!trimmedUrl) {
        errors.baseUrl = 'Base URL is required.'
      } else if (!isValidHttpUrl(trimmedUrl)) {
        errors.baseUrl =
          'Enter a valid http:// or https:// URL without embedded credentials.'
      }

      if (values.authType === 'api_key' && !values.apiKey.trim()) {
        errors.apiKey = 'API key is required for API Key authentication.'
      }
      if (values.authType === 'bearer' && !values.bearerToken.trim()) {
        errors.bearerToken =
          'Bearer token is required for Bearer Token authentication.'
      }
      break
    }

    case 'CSV':
    case 'JSON': {
      const fileError = validateSelectedTestFile(selectedFile, connection.type)
      if (fileError) {
        errors.file = fileError
      }
      break
    }
  }

  return errors
}

export function buildConnectionTestRequest(
  connection: Connection,
  values: ConnectionTestFormValues,
  filePayload?: { fileName: string; fileContent: string },
): ConnectionTestRequest {
  switch (connection.type) {
    case 'PostgreSQL':
    case 'MySQL':
      return {
        connectionId: connection.id,
        connectorType: connection.type,
        config: {
          host: values.host.trim(),
          port: Number(values.port.trim()),
          database: values.database.trim(),
          username: values.username.trim(),
        },
        ...(values.password.length > 0
          ? { credentials: { password: values.password } }
          : {}),
      }

    case 'MongoDB': {
      const username = values.mongoUsername.trim()
      const hasPassword = values.mongoPassword.length > 0
      const transientUri = values.mongoConnectionUri.trim()
      const hasCredentials = hasPassword || transientUri.length > 0

      return {
        connectionId: connection.id,
        connectorType: 'MongoDB',
        config: {
          uri: values.mongoUri.trim(),
          database: values.mongoDatabase.trim(),
          ...(username ? { username } : {}),
        },
        ...(hasCredentials
          ? {
              credentials: {
                ...(hasPassword ? { password: values.mongoPassword } : {}),
                ...(transientUri ? { connectionUri: transientUri } : {}),
              },
            }
          : {}),
      }
    }

    case 'REST API': {
      const apiKey = values.apiKey.trim()
      const bearerToken = values.bearerToken.trim()

      return {
        connectionId: connection.id,
        connectorType: 'REST API',
        config: {
          baseUrl: values.baseUrl.trim(),
          authType: values.authType,
        },
        ...(values.authType === 'api_key' && apiKey
          ? { credentials: { apiKey } }
          : values.authType === 'bearer' && bearerToken
            ? { credentials: { bearerToken } }
            : {}),
      }
    }

    case 'CSV':
    case 'JSON': {
      const fileName = filePayload?.fileName.trim() || values.fileName.trim()
      const safeEndpoint =
        connection.endpoint && !hasEmbeddedUriCredentials(connection.endpoint)
          ? connection.endpoint
          : undefined

      return {
        connectionId: connection.id,
        connectorType: connection.type,
        config: {
          fileName,
          ...(safeEndpoint ? { endpoint: safeEndpoint } : {}),
        },
        fileContent: filePayload?.fileContent ?? '',
      }
    }
  }
}
