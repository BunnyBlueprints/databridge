import type {
  Connection,
  ConnectionAuthType,
  ConnectionNonSecretConfig,
  CreateConnectionPayload,
  CredentialAction,
  FilePersistentConfig,
  UpdateConnectionPayload,
} from '../../types/connection.ts'
import type { ConnectorType } from '../../types/dashboard.ts'

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
  mongoPassword: string
  clearCredentials: boolean
  // REST API
  baseUrl: string
  authType: RestAuthType
  apiKey: string
  bearerToken: string
  // CSV / JSON (bare filename only; file content is never persisted)
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
  return raw.replace(/:\/\/[^\s/]+@/, '://')
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
    mongoPassword: '',
    clearCredentials: false,
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
  const cfg = connection.config

  base.name = connection.name
  base.description = connection.description ?? ''

  switch (connection.type) {
    case 'PostgreSQL':
    case 'MySQL': {
      const sqlCfg = cfg && 'host' in cfg ? cfg : undefined
      const parsed = parseSqlEndpoint(connection.endpoint)
      base.host = sqlCfg?.host ?? d?.host ?? parsed.host ?? ''
      base.port =
        sqlCfg?.port !== undefined
          ? String(sqlCfg.port)
          : d?.port !== undefined
            ? String(d.port)
            : (parsed.port ?? getDefaultPort(connection.type))
      base.database = sqlCfg?.database ?? d?.database ?? parsed.database ?? ''
      // Security: Never prefill stored username or password from backend.
      base.username = ''
      base.password = ''
      break
    }

    case 'MongoDB': {
      const mongoCfg = cfg && 'uri' in cfg ? cfg : undefined
      const parsed = parseMongoEndpoint(connection.endpoint)
      const database = mongoCfg?.database ?? d?.database ?? parsed.database ?? ''
      const rawUri = sanitizeDisplayEndpoint(
        mongoCfg?.uri ?? d?.sanitizedUri ?? parsed.sanitizedUri,
      )
      const withoutDb =
        !mongoCfg?.uri && database && rawUri.endsWith(`/${database}`)
          ? rawUri.slice(0, -(`/${database}`.length))
          : rawUri
      base.mongoUri =
        withoutDb &&
        !withoutDb.startsWith('mongodb://') &&
        !withoutDb.startsWith('mongodb+srv://')
          ? `mongodb+srv://${withoutDb}`
          : withoutDb
      base.mongoDatabase = database
      // Security: Never prefill stored MongoDB username or password.
      base.mongoUsername = ''
      base.mongoPassword = ''
      base.clearCredentials = false
      break
    }

    case 'REST API': {
      const restCfg = cfg && 'baseUrl' in cfg ? cfg : undefined
      base.baseUrl = sanitizeDisplayEndpoint(
        restCfg?.baseUrl ?? d?.baseUrl ?? connection.endpoint,
      )
      base.authType = restCfg?.authType ?? d?.authType ?? 'none'
      // Never prefill API key or bearer token.
      base.apiKey = ''
      base.bearerToken = ''
      break
    }

    case 'CSV':
    case 'JSON': {
      const fileCfg = cfg && 'fileName' in cfg ? cfg : undefined
      base.fileName =
        fileCfg?.fileName ??
        d?.fileName ??
        extractFileName(connection.endpoint) ??
        ''
      break
    }
  }

  return base
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

function hasEmbeddedUriCredentials(raw: string): boolean {
  return /:\/\/[^\s/]+@/.test(raw.trim())
}

function isValidMongoBaseUri(raw: string): boolean {
  const trimmed = raw.trim()
  if (
    !(
      trimmed.startsWith('mongodb://') ||
      trimmed.startsWith('mongodb+srv://')
    ) ||
    trimmed.length <= 'mongodb://'.length
  ) {
    return false
  }
  return !/\s/.test(trimmed)
}

function hasSensitiveMongoQueryParam(raw: string): boolean {
  const queryStart = raw.indexOf('?')
  if (queryStart === -1) return false
  const query = raw.slice(queryStart + 1)
  return /(^|&)(password|passwd|pwd|secret|token|api_key|apikey|bearer|bearertoken|access_token|refresh_token|aws_session_token)=/i.test(
    query,
  )
}

function isValidBareFileName(raw: string, expectedExt: '.csv' | '.json'): boolean {
  const trimmed = raw.trim()
  const lower = trimmed.toLowerCase()
  if (!trimmed || !lower.endsWith(expectedExt)) return false
  if (
    trimmed.includes('/') ||
    trimmed.includes('\\') ||
    trimmed.includes(':') ||
    trimmed.includes('..') ||
    trimmed.startsWith('~') ||
    lower.includes('%2f') ||
    lower.includes('%5c')
  ) {
    return false
  }
  return true
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
  } else if (values.name.trim().length > 120) {
    errors.name = 'Connection name must be 120 characters or fewer.'
  }

  switch (values.type) {
    case 'PostgreSQL':
    case 'MySQL': {
      const host = values.host.trim()
      if (!host) {
        errors.host = 'Host is required.'
      } else if (/[/@?#\s]|:\/\//.test(host)) {
        errors.host = 'Enter a hostname or IP without scheme, path, or credentials.'
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

      const trimmedUser = values.username.trim()
      if (!isEdit) {
        if (!trimmedUser) {
          errors.username = 'Username is required.'
        }
      } else if (values.password.length > 0 && !trimmedUser) {
        errors.username =
          'Username is required when replacing SQL credentials.'
      }
      break
    }

    case 'MongoDB': {
      const uri = values.mongoUri.trim()
      if (!uri) {
        errors.mongoUri = 'Connection URI is required.'
      } else if (!isValidMongoBaseUri(uri)) {
        errors.mongoUri =
          'URI must start with mongodb:// or mongodb+srv:// and contain no spaces.'
      } else if (hasEmbeddedUriCredentials(uri)) {
        errors.mongoUri =
          'URI must not contain embedded credentials; enter username and password below.'
      } else if (hasSensitiveMongoQueryParam(uri)) {
        errors.mongoUri =
          'URI must not include secret query parameters.'
      }

      if (!values.mongoDatabase.trim()) {
        errors.mongoDatabase = 'Database name is required.'
      }
      break
    }

    case 'REST API': {
      const url = values.baseUrl.trim()
      if (!url) {
        errors.baseUrl = 'Base URL is required.'
      } else if (!isValidHttpUrl(url)) {
        errors.baseUrl =
          'Enter a valid http:// or https:// URL without embedded credentials.'
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
      } else if (!isValidBareFileName(values.fileName, expectedExt)) {
        errors.fileName = `Enter a valid ${expectedExt} filename without local paths.`
      }
      break
    }
  }

  return errors
}

function buildPersistentConfig(
  values: ConnectorFormValues,
  existing?: Connection | null,
): ConnectionNonSecretConfig {
  switch (values.type) {
    case 'PostgreSQL':
    case 'MySQL':
      return {
        host: values.host.trim(),
        port: Number(values.port.trim()),
        database: values.database.trim(),
      }

    case 'MongoDB':
      return {
        uri: values.mongoUri.trim(),
        database: values.mongoDatabase.trim(),
      }

    case 'REST API':
      return {
        baseUrl: values.baseUrl.trim(),
        authType: values.authType,
      }

    case 'CSV':
    case 'JSON': {
      const fileName = values.fileName.trim()
      const existingFileCfg =
        existing?.config && 'fileName' in existing.config
          ? existing.config
          : undefined
      const safeExistingEndpoint =
        existingFileCfg?.endpoint &&
        !existingFileCfg.endpoint.startsWith('local://')
          ? existingFileCfg.endpoint
          : undefined

      const fileCfg: FilePersistentConfig = {
        fileName,
        ...(safeExistingEndpoint &&
        existingFileCfg?.fileName === fileName
          ? { endpoint: safeExistingEndpoint }
          : {}),
      }
      return fileCfg
    }
  }
}

export function buildCreateConnectionPayload(
  values: ConnectorFormValues,
): CreateConnectionPayload {
  const name = values.name.trim()
  const description = values.description.trim()
  const descField = description ? { description } : {}

  switch (values.type) {
    case 'PostgreSQL':
    case 'MySQL':
      return {
        name,
        ...descField,
        connectorType: values.type,
        config: {
          host: values.host.trim(),
          port: Number(values.port.trim()),
          database: values.database.trim(),
        },
        credentials: {
          username: values.username.trim(),
          ...(values.password.length > 0 ? { password: values.password } : {}),
        },
      }

    case 'MongoDB': {
      const username = values.mongoUsername.trim()
      const hasPassword = values.mongoPassword.length > 0
      const hasCredentials = username.length > 0 || hasPassword

      return {
        name,
        ...descField,
        connectorType: 'MongoDB',
        config: {
          uri: values.mongoUri.trim(),
          database: values.mongoDatabase.trim(),
        },
        ...(hasCredentials
          ? {
              credentials: {
                ...(username ? { username } : {}),
                ...(hasPassword ? { password: values.mongoPassword } : {}),
              },
            }
          : {}),
      }
    }

    case 'REST API': {
      const apiKey = values.apiKey.trim()
      const bearerToken = values.bearerToken.trim()

      return {
        name,
        ...descField,
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
    case 'JSON':
      return {
        name,
        ...descField,
        connectorType: values.type,
        config: {
          fileName: values.fileName.trim(),
        },
      }
  }
}

export function buildUpdateConnectionPayload(
  values: ConnectorFormValues,
  existing: Connection,
): UpdateConnectionPayload {
  const name = values.name.trim()
  const trimmedDesc = values.description.trim()
  const config = buildPersistentConfig(values, existing)

  const base: UpdateConnectionPayload = {
    name,
    connectorType: existing.type,
    description: trimmedDesc || null,
    config,
    credentialAction: 'keep',
  }

  switch (existing.type) {
    case 'PostgreSQL':
    case 'MySQL': {
      const username = values.username.trim()
      if (username.length > 0) {
        return {
          ...base,
          credentialAction: 'replace',
          credentials: {
            username,
            ...(values.password.length > 0 ? { password: values.password } : {}),
          },
        }
      }
      return base
    }

    case 'MongoDB': {
      if (values.clearCredentials) {
        return {
          ...base,
          credentialAction: 'clear',
        }
      }
      const username = values.mongoUsername.trim()
      const hasPassword = values.mongoPassword.length > 0
      if (username.length > 0 || hasPassword) {
        return {
          ...base,
          credentialAction: 'replace',
          credentials: {
            ...(username ? { username } : {}),
            ...(hasPassword ? { password: values.mongoPassword } : {}),
          },
        }
      }
      return base
    }

    case 'REST API': {
      if (values.authType === 'none') {
        const action: CredentialAction = existing.details?.authConfigured
          ? 'clear'
          : 'keep'
        return {
          ...base,
          credentialAction: action,
        }
      }

      if (values.authType === 'api_key' && values.apiKey.trim()) {
        return {
          ...base,
          credentialAction: 'replace',
          credentials: { apiKey: values.apiKey.trim() },
        }
      }

      if (values.authType === 'bearer' && values.bearerToken.trim()) {
        return {
          ...base,
          credentialAction: 'replace',
          credentials: { bearerToken: values.bearerToken.trim() },
        }
      }

      return base
    }

    case 'CSV':
    case 'JSON':
      return base
  }
}

export function stripSensitiveFormValues(
  values: ConnectorFormValues,
): ConnectorFormValues {
  return {
    ...values,
    username: '',
    password: '',
    mongoUsername: '',
    mongoPassword: '',
    clearCredentials: false,
    apiKey: '',
    bearerToken: '',
  }
}
