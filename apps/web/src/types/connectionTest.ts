import type { ConnectionAuthType } from './connection'
import type { ConnectorType } from './dashboard'

/**
 * Lifecycle states for a connection test operation.
 */
export type ConnectionTestStatus =
  | 'idle'
  | 'testing'
  | 'connected'
  | 'failed'
  | 'timeout'

/**
 * Terminal states returned by a completed connection test request.
 */
export type ConnectionTestTerminalStatus = Exclude<
  ConnectionTestStatus,
  'idle' | 'testing'
>

/**
 * Normalized failure error codes expected from the backend or client transport layer.
 */
export type KnownConnectionTestErrorCode =
  | 'CONNECTION_REFUSED'
  | 'DNS_RESOLUTION_FAILED'
  | 'AUTHENTICATION_FAILED'
  | 'TIMEOUT'
  | 'INVALID_CONFIGURATION'
  | 'UNSUPPORTED_CONNECTOR'
  | 'TESTER_NOT_IMPLEMENTED'
  | 'SERVER_ERROR'
  | 'NETWORK_ERROR'

/**
 * Normalized connection test error code.
 * Preserves unknown backend codes safely while providing autocomplete for known codes.
 */
export type ConnectionTestErrorCode =
  | KnownConnectionTestErrorCode
  | (string & Record<never, never>)

/**
 * Configuration required to test a PostgreSQL or MySQL connection.
 */
export interface SqlConnectionTestConfig {
  host: string
  port: number
  database: string
  username: string
}

/**
 * Transient credentials for SQL database tests.
 * Never persisted in browser storage or logged.
 */
export interface SqlConnectionTestCredentials {
  password?: string
}

/**
 * Non-secret configuration required to test a MongoDB connection.
 */
export interface MongoDbConnectionTestConfig {
  uri: string
  database: string
  username?: string
}

/**
 * Transient credentials for MongoDB connection tests.
 * Supports either a password or a transient full URI with inline credentials.
 * Never persisted in browser storage or logged.
 */
export interface MongoDbConnectionTestCredentials {
  password?: string
  connectionUri?: string
}

/**
 * Non-secret configuration required to test a REST API connection.
 */
export interface RestApiConnectionTestConfig {
  baseUrl: string
  authType: ConnectionAuthType
}

/**
 * Transient credentials for REST API connection tests.
 * Never persisted in browser storage or logged.
 */
export interface RestApiConnectionTestCredentials {
  apiKey?: string
  bearerToken?: string
}

/**
 * Configuration required to test a file-based connector (CSV or JSON).
 */
export interface FileConnectionTestConfig {
  fileName: string
  endpoint?: string
}

interface ConnectionTestRequestBase {
  /**
   * Optional ID of an existing saved connection when testing from connection details.
   * When omitted, the request represents an unsaved/inline connector configuration.
   */
  connectionId?: string
}

export interface PostgresConnectionTestRequest extends ConnectionTestRequestBase {
  connectorType: Extract<ConnectorType, 'PostgreSQL'>
  config: SqlConnectionTestConfig
  credentials?: SqlConnectionTestCredentials
}

export interface MysqlConnectionTestRequest extends ConnectionTestRequestBase {
  connectorType: Extract<ConnectorType, 'MySQL'>
  config: SqlConnectionTestConfig
  credentials?: SqlConnectionTestCredentials
}

export interface MongoDbConnectionTestRequest extends ConnectionTestRequestBase {
  connectorType: Extract<ConnectorType, 'MongoDB'>
  config: MongoDbConnectionTestConfig
  credentials?: MongoDbConnectionTestCredentials
}

export interface RestApiConnectionTestRequest extends ConnectionTestRequestBase {
  connectorType: Extract<ConnectorType, 'REST API'>
  config: RestApiConnectionTestConfig
  credentials?: RestApiConnectionTestCredentials
}

export interface CsvConnectionTestRequest extends ConnectionTestRequestBase {
  connectorType: Extract<ConnectorType, 'CSV'>
  config: FileConnectionTestConfig
  /**
   * Transient CSV file contents transmitted strictly in the test request body.
   * Never persisted in React state, localStorage, or sessionStorage.
   */
  fileContent: string
  credentials?: never
}

export interface JsonConnectionTestRequest extends ConnectionTestRequestBase {
  connectorType: Extract<ConnectorType, 'JSON'>
  config: FileConnectionTestConfig
  /**
   * Transient JSON file contents transmitted strictly in the test request body.
   * Never persisted in React state, localStorage, or sessionStorage.
   */
  fileContent: string
  credentials?: never
}

/**
 * Discriminated union request payload for POST /api/v1/connections/test.
 *
 * Limitations:
 * - The frontend does not persist passwords, API keys, bearer tokens, or MongoDB URI
 *   credentials. Callers may supply transient credentials per test call.
 * - CSV and JSON tests transmit transient `fileContent` to the backend solely for
 *   the active test request and never retain file content after the request completes.
 */
export type ConnectionTestRequest =
  | PostgresConnectionTestRequest
  | MysqlConnectionTestRequest
  | MongoDbConnectionTestRequest
  | RestApiConnectionTestRequest
  | CsvConnectionTestRequest
  | JsonConnectionTestRequest

/**
 * Normalized, secret-free result returned by the connection test service.
 */
export interface ConnectionTestResult {
  success: boolean
  status: ConnectionTestTerminalStatus
  message: string
  errorCode?: ConnectionTestErrorCode
  durationMs?: number
  testedAt?: string
}
