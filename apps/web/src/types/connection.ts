import type { ConnectorType } from './dashboard'

export type ConnectionStatus = 'Connected' | 'Disconnected' | 'Error'

export type ConnectionAuthType = 'none' | 'api_key' | 'bearer'

/**
 * Non-secret configuration metadata safe to display in ConnectionDetailsModal.
 * Passwords, API keys, bearer tokens, and raw credential URIs must never be stored here.
 */
export interface ConnectionSafeDetails {
  host?: string
  port?: number
  database?: string
  username?: string
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
  description?: string
  lastSuccessfulSync?: string
  syncFrequency?: string
  errorMessage?: string
  details?: ConnectionSafeDetails
}
