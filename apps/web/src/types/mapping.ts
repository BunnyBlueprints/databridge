import type { ConnectorType } from './dashboard.ts'

export type MappingStatus = 'draft' | 'valid' | 'invalid'

export type MappingRuleType = 'direct' | 'constant' | 'transform'

export type MappingIssueSeverity = 'error' | 'warning'

export type ScalarJsonValue = string | number | boolean | null

export type ConstantJsonValue =
  | ScalarJsonValue
  | unknown[]
  | Record<string, unknown>

export interface TransformReference {
  transformId?: string | null
  presetName?: string | null
  params?: Record<string, ScalarJsonValue>
}

export interface MappingRuleInput {
  id?: string | null
  sourceFieldPath?: string | null
  destinationFieldPath: string
  mappingType: MappingRuleType
  constantValue?: ConstantJsonValue
  transformRef?: TransformReference | null
  ordinalPosition: number
  metadata?: Record<string, unknown>
}

export interface MappingRule {
  id: string
  mappingId: string
  sourceFieldPath: string | null
  destinationFieldPath: string
  mappingType: MappingRuleType
  constantValue: ConstantJsonValue
  transformRef: TransformReference | null
  ordinalPosition: number
  metadata: Record<string, unknown>
  createdAt: string
  updatedAt: string
}

export interface MappingValidationIssue {
  severity: MappingIssueSeverity
  code: string
  message: string
  destinationFieldPath?: string | null
  sourceFieldPath?: string | null
  ruleId?: string | null
  ordinalPosition?: number | null
}

export interface MappingValidationSummary {
  isValid: boolean
  errorCount: number
  warningCount: number
  missingRequiredDestinationFields: string[]
  unmappedSourceFields: string[]
  unmappedDestinationFields: string[]
  schemaDriftDetected: boolean
  sourceDriftDetected?: boolean
  destinationDriftDetected?: boolean
  issues: MappingValidationIssue[]
  validatedAt: string
}

export interface MappingValidationResponse {
  mappingId: string | null
  mappingVersion: number | null
  status: MappingStatus
  validationSummary: MappingValidationSummary
}

export interface MappingRecord {
  id: string
  name: string
  description: string | null
  sourceConnectionId: string
  sourceObjectKey: string
  destinationConnectionId: string
  destinationObjectKey: string
  status: MappingStatus
  mappingVersion: number
  sourceSchemaFingerprint: string | null
  destinationSchemaFingerprint: string | null
  validationSummary: MappingValidationSummary | null
  rules: MappingRule[]
  createdAt: string
  updatedAt: string
}

export interface MappingListResponse {
  items: MappingRecord[]
  total: number
  limit: number
  offset: number
}

export interface ListMappingsParams {
  sourceConnectionId?: string
  destinationConnectionId?: string
  status?: MappingStatus
  q?: string
  limit?: number
  offset?: number
}

export interface CreateMappingPayload {
  name: string
  description?: string | null
  sourceConnectionId: string
  sourceObjectKey: string
  destinationConnectionId: string
  destinationObjectKey: string
  sourceSchemaFingerprint?: string | null
  destinationSchemaFingerprint?: string | null
  rules: MappingRuleInput[]
}

export interface UpdateMappingPayload {
  name?: string
  description?: string | null
  sourceConnectionId?: string
  sourceObjectKey?: string
  destinationConnectionId?: string
  destinationObjectKey?: string
  sourceSchemaFingerprint?: string | null
  destinationSchemaFingerprint?: string | null
  expectedVersion?: number
  rules?: MappingRuleInput[]
}

export interface MappingConnectionOption {
  id: string
  name: string
  type: ConnectorType
}
