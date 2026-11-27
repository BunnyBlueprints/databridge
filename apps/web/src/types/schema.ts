import type { ConnectorType } from './dashboard.ts'

export type DiscoveryStatus = 'completed' | 'partial'

export type FieldDiscoverySource =
  | 'catalog'
  | 'openapi'
  | 'inferred_sample'
  | 'manual'

export type SnapshotDiscoverySource = FieldDiscoverySource | 'mixed'

export type SchemaObjectType =
  | 'table'
  | 'view'
  | 'collection'
  | 'endpoint'
  | 'file'
  | 'record_set'

export type CanonicalFieldType =
  | 'string'
  | 'integer'
  | 'number'
  | 'boolean'
  | 'date'
  | 'datetime'
  | 'object'
  | 'array'
  | 'binary'
  | 'json'
  | 'null'
  | 'mixed'
  | 'unknown'

export type RestDiscoveryMode = 'openapi' | 'sample_get' | 'manual'

export interface SchemaWarning {
  code: string
  message: string
  objectKey?: string | null
  fieldPath?: string | null
}

export interface SchemaDefaultMetadata {
  hasDefault: boolean
  expression?: string | null
  isGenerated?: boolean
}

export interface ForeignKeyMetadata {
  targetObjectKey: string
  targetFieldPath: string
}

export interface SchemaFieldMetadata {
  depth?: number
  isArray?: boolean
  parentPath?: string
  observedRecords?: number
  sampleSize?: number
  inferredTypes?: CanonicalFieldType[]
  foreignKey?: ForeignKeyMetadata
  warnings?: string[]
  [key: string]: unknown
}

export interface SchemaField {
  id: string
  schemaObjectId: string
  fieldPath: string
  fieldName: string
  nativeType: string
  canonicalType: CanonicalFieldType
  nullable: boolean
  isRequired: boolean
  primaryKey: boolean
  uniqueKey: boolean
  defaultMetadata?: SchemaDefaultMetadata | null
  ordinalPosition: number
  discoverySource: FieldDiscoverySource
  metadata: SchemaFieldMetadata
  createdAt: string
  updatedAt: string
}

export interface SchemaObjectSummary {
  id: string
  discoveredSchemaId: string
  objectKey: string
  objectName: string
  objectType: SchemaObjectType
  discoverySource: FieldDiscoverySource
  nativeMetadata: Record<string, unknown>
  canonicalMetadata: {
    confidence?: number
    primaryKeyFields?: string[]
    [key: string]: unknown
  }
  fieldCount: number
  createdAt: string
  updatedAt: string
}

export interface DiscoveredSchemaResponse {
  id: string
  connectionId: string
  discoveryStatus: DiscoveryStatus
  discoveredAt: string
  schemaFingerprint: string
  discoverySource: SnapshotDiscoverySource
  objectCount: number
  warnings: SchemaWarning[]
  metadata: Record<string, unknown>
  objects: SchemaObjectSummary[]
  createdAt: string
  updatedAt: string
}

export interface DiscoverSchemaResponse {
  snapshotId: string
  connectionId: string
  discoveryStatus: DiscoveryStatus
  discoverySource: SnapshotDiscoverySource
  schemaFingerprint: string
  objectCount: number
  warnings: SchemaWarning[]
  discoveredAt: string
  objects: SchemaObjectSummary[]
}

export interface SchemaObjectListResponse {
  connectionId: string
  schemaId: string
  items: SchemaObjectSummary[]
  total: number
  limit: number
  offset: number
}

export interface SchemaObjectFieldsResponse {
  connectionId: string
  schemaId: string
  object: SchemaObjectSummary
  fields: SchemaField[]
  total: number
  limit: number
  offset: number
}

export interface ManualSchemaFieldInput {
  fieldPath: string
  fieldName?: string
  canonicalType: CanonicalFieldType
  nativeType?: string
  nullable: boolean
  isRequired: boolean
  primaryKey?: boolean
  uniqueKey?: boolean
  defaultMetadata?: SchemaDefaultMetadata
  metadata?: Record<string, unknown>
}

export interface ManualSchemaObjectInput {
  objectKey: string
  objectName?: string
  objectType?: SchemaObjectType
  nativeMetadata?: Record<string, unknown>
  fields: ManualSchemaFieldInput[]
}

export interface DiscoveryOptionsInput {
  includeSchemas?: string[]
  includeViews?: boolean
  sampleLimit?: number
  mode?: RestDiscoveryMode
  openApiPath?: string
  resourcePath?: string
  recordPath?: string
  openApiSpec?: Record<string, unknown>
  manualObjects?: ManualSchemaObjectInput[]
}

export interface DiscoverConnectionSchemaPayload {
  fileName?: string
  fileContent?: string
  options?: DiscoveryOptionsInput
}

export interface ListSchemaObjectsParams {
  q?: string
  objectType?: SchemaObjectType
  objectKey?: string
  limit?: number
  offset?: number
}

export interface ListSchemaFieldsParams {
  q?: string
  limit?: number
  offset?: number
}

export type SupportedConnectorType = ConnectorType
