import type { ConnectionTestTerminalStatus } from './connectionTest.ts'
import type { ConnectorType } from './dashboard.ts'

export type ConnectionStatus = 'Connected' | 'Disconnected' | 'Error'

export type ConnectionAuthType = 'none' | 'api_key' | 'bearer'

export type CredentialAction = 'keep' | 'replace' | 'clear'

/**
 * Non-secret SQL configuration stored in backend JSONB.
 * Usernames and passwords are stored only inside encrypted backend credentials.
 */
export interface SqlPersistentConfig {
  host: string
  port: number
  database: string
}

/**
 * Encrypted SQL credentials payload accepted on create/replace.
 */
export interface SqlPersistentCredentials {
  username: string
  password?: string
}

/**
 * Authoritative credential-free MongoDB configuration stored in backend JSONB.
 */
export interface MongoDbPersistentConfig {
  uri: string
  database: string
}

/**
 * Encrypted MongoDB credentials payload accepted on create/replace.
 */
export interface MongoDbPersistentCredentials {
  username?: string
  password?: string
}

/**
 * Non-secret REST API configuration stored in backend JSONB.
 */
export interface RestApiPersistentConfig {
  baseUrl: string
  authType: ConnectionAuthType
}

/**
 * Encrypted REST API credentials payload accepted on create/replace.
 */
export interface RestApiPersistentCredentials {
  apiKey?: string
  bearerToken?: string
}

/**
 * Non-secret persistent CSV/JSON metadata (never contains fileContent or local paths).
 */
export interface FilePersistentConfig {
  fileName: string
  endpoint?: string
}

export type ConnectionNonSecretConfig =
  | SqlPersistentConfig
  | MongoDbPersistentConfig
  | RestApiPersistentConfig
  | FilePersistentConfig

/**
 * Non-secret configuration metadata safe to expose in frontend Connection objects.
 * Passwords, usernames, API keys, bearer tokens, and raw credential URIs must never live here.
 */
export interface ConnectionSafeDetails {
  host?: string
  port?: number
  database?: string
  sanitizedUri?: string
  baseUrl?: string
  authType?: ConnectionAuthType
  authConfigured?: boolean
  fileName?: string
}

export interface Connection {
  id: string
  name: string
  type: ConnectorType
  status: ConnectionStatus
  endpoint: string
  config?: ConnectionNonSecretConfig
  description?: string | null
  lastSuccessfulSync?: string | null
  syncFrequency?: string | null
  errorMessage?: string | null
  lastTestedAt?: string | null
  lastTestStatus?: ConnectionTestTerminalStatus | null
  createdAt?: string
  updatedAt?: string
  details?: ConnectionSafeDetails
}

interface CreateConnectionBase {
  name: string
  description?: string | null
}

export interface CreatePostgresConnectionPayload extends CreateConnectionBase {
  connectorType: Extract<ConnectorType, 'PostgreSQL'>
  config: SqlPersistentConfig
  credentials: SqlPersistentCredentials
}

export interface CreateMysqlConnectionPayload extends CreateConnectionBase {
  connectorType: Extract<ConnectorType, 'MySQL'>
  config: SqlPersistentConfig
  credentials: SqlPersistentCredentials
}

export interface CreateMongoDbConnectionPayload extends CreateConnectionBase {
  connectorType: Extract<ConnectorType, 'MongoDB'>
  config: MongoDbPersistentConfig
  credentials?: MongoDbPersistentCredentials
}

export interface CreateRestApiConnectionPayload extends CreateConnectionBase {
  connectorType: Extract<ConnectorType, 'REST API'>
  config: RestApiPersistentConfig
  credentials?: RestApiPersistentCredentials
}

export interface CreateCsvConnectionPayload extends CreateConnectionBase {
  connectorType: Extract<ConnectorType, 'CSV'>
  config: FilePersistentConfig
}

export interface CreateJsonConnectionPayload extends CreateConnectionBase {
  connectorType: Extract<ConnectorType, 'JSON'>
  config: FilePersistentConfig
}

export type CreateConnectionPayload =
  | CreatePostgresConnectionPayload
  | CreateMysqlConnectionPayload
  | CreateMongoDbConnectionPayload
  | CreateRestApiConnectionPayload
  | CreateCsvConnectionPayload
  | CreateJsonConnectionPayload

export type ConnectionCredentialsReplacePayload =
  | SqlPersistentCredentials
  | MongoDbPersistentCredentials
  | RestApiPersistentCredentials

export interface UpdateConnectionPayload {
  name?: string
  connectorType?: ConnectorType
  description?: string | null
  config?: ConnectionNonSecretConfig
  credentialAction?: CredentialAction
  credentials?: ConnectionCredentialsReplacePayload
}

export interface ConnectionListResponse {
  items: Connection[]
  total: number
  limit: number
  offset: number
}

export interface ListConnectionsParams {
  status?: ConnectionStatus
  type?: ConnectorType
  q?: string
  limit?: number
  offset?: number
}

export interface SavedConnectionTestPayload {
  fileContent?: string
}

export interface ApiErrorResponse {
  errorCode: string
  message: string
  timestamp?: string
}
