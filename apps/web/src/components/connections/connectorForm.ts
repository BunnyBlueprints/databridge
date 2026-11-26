import type { Connection, ConnectionAuthType } from '../../types/connection'
import type { ConnectorType } from '../../types/dashboard'

export type RestAuthType = ConnectionAuthType

export type ConnectionFormMode = 'create' | 'edit'

export interface ConnectorFormValues {
  name: string
  type: ConnectorType
  description: string
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
  mongoReplaceCredentials: string
  // REST API
  baseUrl: string
  authType: RestAuthType
  apiKey: string
  bearerToken: string
  // CSV / JSON
  fileName: string
}

export type FormErrors = Partial<Record<keyof ConnectorFormValues, string>>

export interface ValidateFormOptions {
  mode?: ConnectionFormMode
  existingConnection?: Connection | null
}

export const CONNECTOR_TYPES: ConnectorType[] = [
  'PostgreSQL',
  'MySQL',
  'MongoDB',
  'REST API',
  'CSV',
  'JSON',
]

export function getDefaultPort(type: ConnectorType): string {
  if (type === 'MySQL') return '3306'
  return '5432'
}

export function sanitizeDisplayEndpoint(raw: string): string {
  return raw.replace(/:\/\/[^/@]+@/, '://')
}

export function parseSqlEndpoint(endpoint: string): {
  host?: string
  port?: string
  database?: string
} {
  const match = endpoint.match(/^([^:/]+):(\d+)\/(.+)$/)
  if (!match) return {}
  return { host: match[1], port: match[2], database: match[3] }
}

export function parseMongoEndpoint(endpoint: string): {
  sanitizedUri: string
  database?: string
} {
  const sanitized = sanitizeDisplayEndpoint(endpoint)
  const lastSlash = sanitized.lastIndexOf('/')
  if (lastSlash === -1 || lastSlash === sanitized.length - 1) {
    return { sanitizedUri: sanitized }
  }
  const database = sanitized.slice(lastSlash + 1)
  return {
    sanitizedUri: sanitized,
    database: database || undefined,
  }
}

export function extractFileName(endpoint: string): string | undefined {
  if (endpoint.endsWith('/')) return undefined
  const withoutScheme = endpoint.replace(/^[a-z]+:\/\//i, '')
  const segments = withoutScheme.split('/').filter(Boolean)
  return segments.length > 0 ? segments[segments.length - 1] : undefined
}

export function createInitialFormValues(
  type: ConnectorType = 'PostgreSQL',
): ConnectorFormValues {
  return {
    name: '',
    type,
    description: '',
    host: '',
    port: getDefaultPort(type),
    database: '',
    username: '',
    password: '',
    mongoUri: '',
    mongoDatabase: '',
    mongoUsername: '',
    mongoReplaceCredentials: '',
    baseUrl: '',
    authType: 'none',
    apiKey: '',
    bearerToken: '',
    fileName: '',
  }
}

export function createFormValuesFromConnection(
  connection: Connection,
): ConnectorFormValues {
  const base = createInitialFormValues(connection.type)
  const d = connection.details

  base.name = connection.name
  base.description = connection.description ?? ''

  switch (connection.type) {
    case 'PostgreSQL':
    case 'MySQL': {
      const parsed = parseSqlEndpoint(connection.endpoint)
      base.host = d?.host ?? parsed.host ?? ''
      base.port =
        d?.port !== undefined
          ? String(d.port)
          : (parsed.port ?? getDefaultPort(connection.type))
      base.database = d?.database ?? parsed.database ?? ''
      base.username = d?.username ?? ''
      // Never prepopulate password; leave empty for optional replacement.
      base.password = ''
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
      base.mongoUri =
        withoutDb &&
        !withoutDb.startsWith('mongodb://') &&
        !withoutDb.startsWith('mongodb+srv://')
          ? `mongodb+srv://${withoutDb}`
          : withoutDb
      base.mongoDatabase = database
      base.mongoUsername = d?.username ?? ''
      base.mongoReplaceCredentials = ''
      break
    }

    case 'REST API': {
      base.baseUrl = sanitizeDisplayEndpoint(d?.baseUrl ?? connection.endpoint)
      base.authType = d?.authType ?? 'none'
      // Never prepopulate API key or bearer token.
      base.apiKey = ''
      base.bearerToken = ''
      break
    }

    case 'CSV':
    case 'JSON': {
      base.fileName = d?.fileName ?? extractFileName(connection.endpoint) ?? ''
      break
    }
  }

  return base
}

function isValidHttpUrl(raw: string): boolean {
  try {
    const parsed = new URL(raw)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
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

function sanitizeMongoEndpoint(rawUri: string, database: string): string {
  // Strip any embedded userinfo (username:password@) before exposing endpoint in UI.
  const withoutCredentials = rawUri.trim().replace(/:\/\/[^/@]+@/, '://')
  const trimmedBase = withoutCredentials.replace(/\/+$/, '')
  return `${trimmedBase}/${database.trim()}`
}

export function validateConnectorForm(
  values: ConnectorFormValues,
  options: ValidateFormOptions = {},
): FormErrors {
  const mode = options.mode ?? 'create'
  const existing = options.existingConnection ?? null
  const isEdit = mode === 'edit'
  const errors: FormErrors = {}

  if (!values.name.trim()) {
    errors.name = 'Connection name is required.'
  }

  switch (values.type) {
    case 'PostgreSQL':
    case 'MySQL': {
      if (!values.host.trim()) {
        errors.host = 'Host is required.'
      }

      const trimmedPort = values.port.trim()
      if (!trimmedPort) {
        errors.port = 'Port is required.'
      } else if (!/^\d+$/.test(trimmedPort)) {
        errors.port = 'Port must be a valid number.'
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

      const hasExistingPassword =
        isEdit && existing?.details?.authConfigured !== false
      if (!hasExistingPassword && !values.password) {
        errors.password = 'Password is required.'
      }
      break
    }

    case 'MongoDB': {
      if (!values.mongoUri.trim()) {
        errors.mongoUri = 'Connection URI is required.'
      } else if (!isValidMongoUri(values.mongoUri)) {
        errors.mongoUri =
          'URI must start with mongodb:// or mongodb+srv://.'
      }
      if (!values.mongoDatabase.trim()) {
        errors.mongoDatabase = 'Database name is required.'
      }
      break
    }

    case 'REST API': {
      if (!values.baseUrl.trim()) {
        errors.baseUrl = 'Base URL is required.'
      } else if (!isValidHttpUrl(values.baseUrl.trim())) {
        errors.baseUrl = 'Enter a valid http:// or https:// URL.'
      }

      const keepingExistingApiKey =
        isEdit &&
        existing?.details?.authType === 'api_key' &&
        existing.details.authConfigured === true
      const keepingExistingBearer =
        isEdit &&
        existing?.details?.authType === 'bearer' &&
        existing.details.authConfigured === true

      if (
        values.authType === 'api_key' &&
        !keepingExistingApiKey &&
        !values.apiKey.trim()
      ) {
        errors.apiKey = 'API key is required for API Key authentication.'
      }
      if (
        values.authType === 'bearer' &&
        !keepingExistingBearer &&
        !values.bearerToken.trim()
      ) {
        errors.bearerToken =
          'Bearer token is required for Bearer Token authentication.'
      }
      break
    }

    case 'CSV':
    case 'JSON': {
      const expectedExt = values.type === 'CSV' ? '.csv' : '.json'
      if (!values.fileName.trim()) {
        errors.fileName = `Select a ${expectedExt} file to configure this source.`
      } else if (!values.fileName.toLowerCase().endsWith(expectedExt)) {
        errors.fileName = `Selected file must have a ${expectedExt} extension.`
      }
      break
    }
  }

  return errors
}

function createConnectionId(name: string): string {
  const slug =
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 20) || 'source'

  const suffix =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID().slice(0, 8)
      : Date.now().toString(36)

  return `conn-${slug}-${suffix}`
}

function buildEndpointAndDetails(
  values: ConnectorFormValues,
  existing?: Connection | null,
): { endpoint: string; details: NonNullable<Connection['details']> } {
  switch (values.type) {
    case 'PostgreSQL':
    case 'MySQL': {
      const host = values.host.trim()
      const port = Number(values.port.trim())
      const database = values.database.trim()
      const username = values.username.trim()
      const preservedAuth =
        existing?.details?.authConfigured ?? (Boolean(values.password) || undefined)

      return {
        endpoint: `${host}:${port}/${database}`,
        details: {
          host,
          port,
          database,
          username,
          authConfigured: values.password ? true : preservedAuth,
        },
      }
    }

    case 'MongoDB': {
      const database = values.mongoDatabase.trim()
      const username = values.mongoUsername.trim()
      const endpoint = sanitizeMongoEndpoint(values.mongoUri, database)
      const hasReplacementCredentials =
        Boolean(values.mongoReplaceCredentials.trim()) ||
        /:\/\/[^/@]+@/.test(values.mongoUri)

      return {
        endpoint,
        details: {
          sanitizedUri: endpoint,
          database,
          ...(username ? { username } : {}),
          authConfigured: hasReplacementCredentials
            ? true
            : (existing?.details?.authConfigured ??
              (Boolean(username) || undefined)),
        },
      }
    }

    case 'REST API': {
      const parsed = new URL(values.baseUrl.trim())
      const endpoint = `${parsed.origin}${parsed.pathname === '/' ? '' : parsed.pathname}`

      const hadNoPriorAuthType =
        existing !== undefined &&
        existing !== null &&
        existing.details?.authType === undefined

      if (hadNoPriorAuthType && values.authType === 'none') {
        return {
          endpoint,
          details: {
            baseUrl: endpoint,
            authType: existing.details?.authType,
            authConfigured: existing.details?.authConfigured,
          },
        }
      }

      const authConfigured =
        values.authType === 'none'
          ? false
          : values.authType === 'api_key'
            ? Boolean(values.apiKey.trim()) ||
              (existing?.details?.authType === 'api_key' &&
                existing.details.authConfigured === true)
            : Boolean(values.bearerToken.trim()) ||
              (existing?.details?.authType === 'bearer' &&
                existing.details.authConfigured === true)

      return {
        endpoint,
        details: {
          baseUrl: endpoint,
          authType: values.authType,
          authConfigured,
        },
      }
    }

    case 'CSV':
    case 'JSON': {
      const fileName = values.fileName.trim()
      const prevFileName =
        existing?.details?.fileName ??
        (existing ? extractFileName(existing.endpoint) : undefined)
      const keepExistingEndpoint =
        existing &&
        prevFileName === fileName &&
        !existing.endpoint.startsWith('local://')

      return {
        endpoint: keepExistingEndpoint ? existing.endpoint : `local://${fileName}`,
        details: {
          fileName,
          authConfigured: existing?.details?.authConfigured ?? false,
        },
      }
    }
  }
}

export function buildConnectionFromForm(values: ConnectorFormValues): Connection {
  const { endpoint, details } = buildEndpointAndDetails(values)
  const trimmedDescription = values.description.trim()

  return {
    id: createConnectionId(values.name),
    name: values.name.trim(),
    type: values.type,
    status: 'Disconnected',
    endpoint,
    ...(trimmedDescription ? { description: trimmedDescription } : {}),
    details,
  }
}

export function buildUpdatedConnectionFromForm(
  values: ConnectorFormValues,
  existing: Connection,
): Connection {
  const { endpoint, details } = buildEndpointAndDetails(values, existing)
  const trimmedDescription = values.description.trim()

  return {
    ...existing,
    name: values.name.trim(),
    type: existing.type,
    status: existing.status,
    endpoint,
    description: trimmedDescription || undefined,
    details: {
      ...existing.details,
      ...details,
    },
  }
}
