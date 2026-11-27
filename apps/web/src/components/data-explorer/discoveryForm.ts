import type { ConnectorType } from '../../types/dashboard.ts'
import type {
  CanonicalFieldType,
  DiscoverConnectionSchemaPayload,
  DiscoveryOptionsInput,
  ManualSchemaFieldInput,
  RestDiscoveryMode,
  SchemaObjectType,
  SnapshotDiscoverySource,
} from '../../types/schema.ts'

/**
 * Mirrors backend CONNECTION_TEST_MAX_FILE_BYTES (256 KB) for early transient upload checks.
 */
export const MAX_DISCOVERY_FILE_BYTES = 262_144

export const CANONICAL_FIELD_TYPES: readonly CanonicalFieldType[] = [
  'string',
  'integer',
  'number',
  'boolean',
  'date',
  'datetime',
  'object',
  'array',
  'binary',
  'json',
  'null',
  'mixed',
  'unknown',
]

export interface ManualFieldDraft {
  fieldPath: string
  canonicalType: CanonicalFieldType
  nativeType: string
  nullable: boolean
  isRequired: boolean
  primaryKey: boolean
  uniqueKey: boolean
}

export interface DiscoveryFormValues {
  includeSchemasText: string
  includeViews: boolean
  sampleLimit: string
  recordPath: string
  restMode: RestDiscoveryMode
  openApiSource: 'path' | 'json'
  openApiPath: string
  openApiJsonText: string
  resourcePath: string
  manualObjectKey: string
  manualObjectName: string
  manualObjectType: SchemaObjectType
  manualFields: ManualFieldDraft[]
}

export interface DiscoveryFileMeta {
  name: string
  size: number
}

export interface DiscoveryFormValidationResult {
  valid: boolean
  errors: Partial<Record<keyof DiscoveryFormValues | 'file' | 'general', string>>
}

export function createEmptyManualField(index = 0): ManualFieldDraft {
  return {
    fieldPath: index === 0 ? 'id' : '',
    canonicalType: 'string',
    nativeType: '',
    nullable: index !== 0,
    isRequired: index === 0,
    primaryKey: index === 0,
    uniqueKey: index === 0,
  }
}

export function createInitialDiscoveryForm(): DiscoveryFormValues {
  return {
    includeSchemasText: '',
    includeViews: true,
    sampleLimit: '100',
    recordPath: '',
    restMode: 'sample_get',
    openApiSource: 'path',
    openApiPath: '/openapi.json',
    openApiJsonText: '',
    resourcePath: '',
    manualObjectKey: 'POST /v1/records',
    manualObjectName: 'records',
    manualObjectType: 'endpoint',
    manualFields: [
      createEmptyManualField(0),
      {
        fieldPath: 'email',
        canonicalType: 'string',
        nativeType: 'string(email)',
        nullable: false,
        isRequired: true,
        primaryKey: false,
        uniqueKey: false,
      },
    ],
  }
}

export function isTransientFileConnector(type: ConnectorType): boolean {
  return type === 'CSV' || type === 'JSON'
}

export function isInferredDiscoverySource(
  source: SnapshotDiscoverySource,
): boolean {
  return source === 'inferred_sample' || source === 'mixed'
}

export function parseIncludeSchemas(raw: string): string[] {
  return raw
    .split(',')
    .map((part) => part.trim())
    .filter((part) => part.length > 0)
}

function validateRelativeHttpPath(raw: string, label: string): string | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  if (/\s/.test(trimmed)) {
    return `${label} must not contain whitespace.`
  }
  if (
    trimmed.includes('://') ||
    trimmed.startsWith('//') ||
    trimmed.includes('@')
  ) {
    return `${label} must be a relative path starting with '/' on the saved REST host.`
  }
  return null
}

function validateRecordPath(raw: string): string | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  if (/\s/.test(trimmed)) {
    return 'Record path must not contain whitespace.'
  }
  if (trimmed.split('.').some((segment) => segment.length === 0)) {
    return 'Record path must not contain empty dot segments.'
  }
  return null
}

function validateSampleLimit(raw: string): number | string {
  const trimmed = raw.trim()
  if (!trimmed) return 100
  if (!/^\d+$/.test(trimmed)) {
    return 'Sample limit must be a positive integer.'
  }
  const parsed = Number(trimmed)
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 1000) {
    return 'Sample limit must be between 1 and 1000.'
  }
  return parsed
}

export function validateDiscoveryForm(
  connectorType: ConnectorType,
  values: DiscoveryFormValues,
  fileMeta: DiscoveryFileMeta | null,
): DiscoveryFormValidationResult {
  const errors: DiscoveryFormValidationResult['errors'] = {}

  if (connectorType === 'PostgreSQL' || connectorType === 'MySQL') {
    const schemas = parseIncludeSchemas(values.includeSchemasText)
    if (schemas.length > 50) {
      errors.includeSchemasText = 'Specify at most 50 schema names.'
    }
    return { valid: Object.keys(errors).length === 0, errors }
  }

  if (connectorType === 'MongoDB') {
    const limitResult = validateSampleLimit(values.sampleLimit)
    if (typeof limitResult === 'string') {
      errors.sampleLimit = limitResult
    }
    return { valid: Object.keys(errors).length === 0, errors }
  }

  if (connectorType === 'CSV' || connectorType === 'JSON') {
    const expectedExt = connectorType === 'CSV' ? '.csv' : '.json'
    if (!fileMeta) {
      errors.file = `Choose a ${expectedExt} file to infer schema metadata.`
    } else {
      if (!fileMeta.name.toLowerCase().endsWith(expectedExt)) {
        errors.file = `Selected file must have a ${expectedExt} extension.`
      } else if (fileMeta.size <= 0) {
        errors.file = 'Selected file is empty.'
      } else if (fileMeta.size > MAX_DISCOVERY_FILE_BYTES) {
        errors.file = `File exceeds the maximum allowed size of ${MAX_DISCOVERY_FILE_BYTES} bytes (256 KB).`
      }
    }

    if (connectorType === 'JSON') {
      const rpError = validateRecordPath(values.recordPath)
      if (rpError) errors.recordPath = rpError
    }

    return { valid: Object.keys(errors).length === 0, errors }
  }

  if (connectorType === 'REST API') {
    if (values.restMode === 'sample_get') {
      const resErr = validateRelativeHttpPath(
        values.resourcePath,
        'Resource path',
      )
      if (resErr) errors.resourcePath = resErr
      const rpErr = validateRecordPath(values.recordPath)
      if (rpErr) errors.recordPath = rpErr
      const limitResult = validateSampleLimit(values.sampleLimit)
      if (typeof limitResult === 'string') {
        errors.sampleLimit = limitResult
      }
    } else if (values.restMode === 'openapi') {
      if (values.openApiSource === 'path') {
        if (!values.openApiPath.trim()) {
          errors.openApiPath =
            'Enter a relative OpenAPI JSON path (e.g. /openapi.json).'
        } else {
          const pathErr = validateRelativeHttpPath(
            values.openApiPath,
            'OpenAPI path',
          )
          if (pathErr) errors.openApiPath = pathErr
        }
      } else {
        const rawSpec = values.openApiJsonText.trim()
        if (!rawSpec) {
          errors.openApiJsonText =
            'Paste an OpenAPI 3.x or Swagger 2.0 JSON specification.'
        } else {
          try {
            const parsed: unknown = JSON.parse(rawSpec)
            if (
              typeof parsed !== 'object' ||
              parsed === null ||
              Array.isArray(parsed) ||
              (!('openapi' in parsed) && !('swagger' in parsed))
            ) {
              errors.openApiJsonText =
                'Specification must be a JSON object with an "openapi" or "swagger" key.'
            }
          } catch {
            errors.openApiJsonText = 'Invalid JSON syntax in OpenAPI specification.'
          }
        }
      }
    } else if (values.restMode === 'manual') {
      if (!values.manualObjectKey.trim()) {
        errors.manualObjectKey =
          'Object key is required (e.g. POST /v1/records).'
      }
      if (values.manualFields.length === 0) {
        errors.manualFields = 'Add at least one field definition.'
      } else {
        const seen = new Set<string>()
        for (const field of values.manualFields) {
          const fp = field.fieldPath.trim()
          if (!fp) {
            errors.manualFields = 'Every manual field must have a field path.'
            break
          }
          if (seen.has(fp)) {
            errors.manualFields = `Duplicate manual field path "${fp}".`
            break
          }
          seen.add(fp)
        }
      }
    }
    return { valid: Object.keys(errors).length === 0, errors }
  }

  return { valid: true, errors }
}

export function buildDiscoverPayload(
  connectorType: ConnectorType,
  values: DiscoveryFormValues,
  transientFile?: { fileName: string; fileContent: string },
): DiscoverConnectionSchemaPayload {
  const options: DiscoveryOptionsInput = {}

  if (connectorType === 'PostgreSQL' || connectorType === 'MySQL') {
    const schemas = parseIncludeSchemas(values.includeSchemasText)
    if (connectorType === 'PostgreSQL' && schemas.length > 0) {
      options.includeSchemas = schemas
    }
    options.includeViews = values.includeViews
    return { options }
  }

  if (connectorType === 'MongoDB') {
    const limitResult = validateSampleLimit(values.sampleLimit)
    if (typeof limitResult === 'number') {
      options.sampleLimit = limitResult
    }
    return { options }
  }

  if (connectorType === 'CSV') {
    if (!transientFile) {
      throw new Error('Transient CSV file content is required for discovery.')
    }
    return {
      fileName: transientFile.fileName,
      fileContent: transientFile.fileContent,
      options: { sampleLimit: 500 },
    }
  }

  if (connectorType === 'JSON') {
    if (!transientFile) {
      throw new Error('Transient JSON file content is required for discovery.')
    }
    const rp = values.recordPath.trim()
    if (rp) {
      options.recordPath = rp
    }
    return {
      fileName: transientFile.fileName,
      fileContent: transientFile.fileContent,
      options,
    }
  }

  if (connectorType === 'REST API') {
    options.mode = values.restMode
    if (values.restMode === 'sample_get') {
      const rpPath = values.resourcePath.trim()
      const recPath = values.recordPath.trim()
      const limitResult = validateSampleLimit(values.sampleLimit)
      if (rpPath) options.resourcePath = rpPath
      if (recPath) options.recordPath = recPath
      if (typeof limitResult === 'number') options.sampleLimit = limitResult
      return { options }
    }

    if (values.restMode === 'openapi') {
      if (values.openApiSource === 'path') {
        const oaPath = values.openApiPath.trim()
        if (oaPath) options.openApiPath = oaPath
      } else {
        const parsedSpec = JSON.parse(values.openApiJsonText.trim()) as Record<
          string,
          unknown
        >
        options.openApiSpec = parsedSpec
      }
      return { options }
    }

    const fields: ManualSchemaFieldInput[] = values.manualFields.map((fld) => ({
      fieldPath: fld.fieldPath.trim(),
      canonicalType: fld.canonicalType,
      ...(fld.nativeType.trim() ? { nativeType: fld.nativeType.trim() } : {}),
      nullable: fld.nullable,
      isRequired: fld.isRequired,
      primaryKey: fld.primaryKey,
      uniqueKey: fld.uniqueKey,
    }))

    options.manualObjects = [
      {
        objectKey: values.manualObjectKey.trim(),
        ...(values.manualObjectName.trim()
          ? { objectName: values.manualObjectName.trim() }
          : {}),
        objectType: values.manualObjectType,
        fields,
      },
    ]
    return { options }
  }

  return { options }
}

export function formatDiscoveryTimestamp(isoString: string): string {
  const date = new Date(isoString)
  if (Number.isNaN(date.getTime())) return isoString
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatObjectConfidence(
  canonicalMetadata: Record<string, unknown>,
): string | null {
  const raw = canonicalMetadata.confidence
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return null
  return `${Math.round(raw * 100)}%`
}
