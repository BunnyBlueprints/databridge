import type {
  ConstantJsonValue,
  MappingRecord,
  MappingRule,
  MappingRuleInput,
  MappingRuleType,
  MappingStatus,
  ScalarJsonValue,
  TransformReference,
  UpdateMappingPayload,
} from '../../types/mapping.ts'
import type { SchemaField } from '../../types/schema.ts'

export const FORBIDDEN_TRANSFORM_PARAM_KEYS = new Set([
  'code',
  'expression',
  'expr',
  'eval',
  'exec',
  'function',
  'lambda',
  'python',
  'script',
  'sql',
  'statement',
])

export const FORBIDDEN_CONSTANT_KEYS = new Set([
  'password',
  'passwd',
  'pwd',
  'secret',
  'apikey',
  'api_key',
  'bearertoken',
  'bearer_token',
  'access_token',
  'refresh_token',
  'connectionuri',
  'connection_uri',
  'credentials',
  'ciphertext',
  'nonce',
  'keyid',
  'key_id',
  'username',
  'filecontent',
  'file_content',
  'rawcontent',
  'raw_content',
  'samplerows',
  'sample_rows',
])

const IDENTIFIER_PATTERN = /^[A-Za-z0-9_.:-]+$/

export interface TransformParamDraft {
  key: string
  value: string
  valueKind: 'string' | 'number' | 'boolean' | 'null'
}

export interface MappingRuleDraft {
  localKey: string
  id?: string | null
  mappingType: MappingRuleType
  sourceFieldPath: string
  destinationFieldPath: string
  constantJsonText: string
  transformPresetName: string
  transformId: string
  transformParams: TransformParamDraft[]
  ordinalPosition: number
  metadata: Record<string, unknown>
}

export interface MappingEditorFormState {
  name: string
  description: string
  sourceConnectionId: string
  sourceObjectKey: string
  sourceSchemaFingerprint: string | null
  destinationConnectionId: string
  destinationObjectKey: string
  destinationSchemaFingerprint: string | null
  rules: MappingRuleDraft[]
}

export function createLocalRuleKey(): string {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return crypto.randomUUID()
  }
  return `rule-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

export function isEffectivelyRequiredDestinationField(
  field: SchemaField,
): boolean {
  if (!field.isRequired || field.nullable) return false
  if (
    field.defaultMetadata &&
    (field.defaultMetadata.hasDefault || field.defaultMetadata.isGenerated)
  ) {
    return false
  }
  const meta = field.metadata
  if (
    Boolean(meta.hasDefault) ||
    Boolean(meta.isGenerated) ||
    Boolean(meta.isIdentity) ||
    Boolean(meta.autoIncrement)
  ) {
    return false
  }
  return true
}

export function getMappingStatusDisplay(status: MappingStatus): {
  label: string
  tone: 'zinc' | 'emerald' | 'rose'
} {
  if (status === 'valid') return { label: 'Valid', tone: 'emerald' }
  if (status === 'invalid') return { label: 'Invalid', tone: 'rose' }
  return { label: 'Draft', tone: 'zinc' }
}

function checkConstantNodeSafety(node: unknown, depth = 0): string | null {
  if (depth > 6) {
    return 'Constant JSON exceeds maximum nesting depth of 6.'
  }
  if (node === null || typeof node === 'boolean') return null
  if (typeof node === 'number') {
    return Number.isFinite(node)
      ? null
      : 'Constant numbers must be finite JSON values.'
  }
  if (typeof node === 'string') {
    if (node.length > 4096) {
      return 'Constant string exceeds maximum length of 4096 characters.'
    }
    if (/:\/\/[^/\s:@]+:[^/\s@]*@/.test(node)) {
      return 'Constant strings must not embed URI credentials.'
    }
    return null
  }
  if (Array.isArray(node)) {
    if (node.length > 200) {
      return 'Constant JSON arrays cannot exceed 200 items.'
    }
    for (const item of node) {
      const err = checkConstantNodeSafety(item, depth + 1)
      if (err) return err
    }
    return null
  }
  if (typeof node === 'object') {
    const entries = Object.entries(node as Record<string, unknown>)
    if (entries.length > 100) {
      return 'Constant JSON objects cannot exceed 100 keys.'
    }
    for (const [key, child] of entries) {
      const lowered = key.trim().toLowerCase()
      if (!lowered) return 'Constant object keys must be non-empty strings.'
      if (
        FORBIDDEN_CONSTANT_KEYS.has(lowered) ||
        FORBIDDEN_TRANSFORM_PARAM_KEYS.has(lowered)
      ) {
        return `Constant JSON key '${key}' is not permitted.`
      }
      const err = checkConstantNodeSafety(child, depth + 1)
      if (err) return err
    }
    return null
  }
  return 'Constant value must be valid JSON (null, primitive, array, or object).'
}

export function parseConstantJsonInput(
  text: string,
):
  | { ok: true; value: ConstantJsonValue }
  | { ok: false; error: string } {
  const trimmed = text.trim()
  if (!trimmed) {
    return {
      ok: false,
      error:
        'Enter valid JSON for constant value (e.g. "value", 42, true, null, ["tag"], {"key": "value"}).',
    }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    return {
      ok: false,
      error:
        'Invalid JSON syntax. Wrap strings in double quotes (e.g. "production") or use null, true, 42, [...], or {...}.',
    }
  }
  const safetyErr = checkConstantNodeSafety(parsed)
  if (safetyErr) {
    return { ok: false, error: safetyErr }
  }
  return { ok: true, value: parsed as ConstantJsonValue }
}

function serializeTransformParams(
  drafts: TransformParamDraft[],
):
  | { ok: true; params: Record<string, ScalarJsonValue> }
  | { ok: false; error: string } {
  const result: Record<string, ScalarJsonValue> = {}
  for (const param of drafts) {
    const key = param.key.trim()
    if (!key) {
      return { ok: false, error: 'Transform parameter keys cannot be empty.' }
    }
    if (FORBIDDEN_TRANSFORM_PARAM_KEYS.has(key.toLowerCase())) {
      return {
        ok: false,
        error: `Executable parameter '${key}' is not permitted. Transforms are structural references only.`,
      }
    }
    if (param.valueKind === 'null') {
      result[key] = null
    } else if (param.valueKind === 'boolean') {
      const normalized = param.value.trim().toLowerCase()
      if (normalized !== 'true' && normalized !== 'false') {
        return {
          ok: false,
          error: `Boolean parameter '${key}' must be true or false.`,
        }
      }
      result[key] = normalized === 'true'
    } else if (param.valueKind === 'number') {
      const num = Number(param.value.trim())
      if (!param.value.trim() || !Number.isFinite(num)) {
        return {
          ok: false,
          error: `Numeric parameter '${key}' must be a valid finite number.`,
        }
      }
      result[key] = num
    } else {
      result[key] = param.value
    }
  }
  return { ok: true, params: result }
}

export function ruleResponseToDraft(rule: MappingRule): MappingRuleDraft {
  const paramDrafts: TransformParamDraft[] = []
  if (rule.transformRef?.params) {
    for (const [k, v] of Object.entries(rule.transformRef.params)) {
      if (v === null) {
        paramDrafts.push({ key: k, value: 'null', valueKind: 'null' })
      } else if (typeof v === 'boolean') {
        paramDrafts.push({
          key: k,
          value: v ? 'true' : 'false',
          valueKind: 'boolean',
        })
      } else if (typeof v === 'number') {
        paramDrafts.push({ key: k, value: String(v), valueKind: 'number' })
      } else {
        paramDrafts.push({ key: k, value: String(v), valueKind: 'string' })
      }
    }
  }

  return {
    localKey: rule.id || createLocalRuleKey(),
    id: rule.id,
    mappingType: rule.mappingType,
    sourceFieldPath: rule.sourceFieldPath ?? '',
    destinationFieldPath: rule.destinationFieldPath,
    constantJsonText:
      rule.mappingType === 'constant'
        ? JSON.stringify(rule.constantValue)
        : '"value"',
    transformPresetName: rule.transformRef?.presetName ?? '',
    transformId: rule.transformRef?.transformId ?? '',
    transformParams: paramDrafts,
    ordinalPosition: rule.ordinalPosition,
    metadata: rule.metadata ?? {},
  }
}

export function serializeRuleDraft(
  draft: MappingRuleDraft,
  ordinalPosition: number,
):
  | { ok: true; rule: MappingRuleInput }
  | { ok: false; error: string } {
  const dstPath = draft.destinationFieldPath.trim()
  if (!dstPath) {
    return {
      ok: false,
      error: 'Destination field path is required for every mapping rule.',
    }
  }

  if (draft.mappingType === 'direct') {
    const srcPath = draft.sourceFieldPath.trim()
    if (!srcPath) {
      return {
        ok: false,
        error: `Direct rule targeting '${dstPath}' requires a source field.`,
      }
    }
    return {
      ok: true,
      rule: {
        ...(draft.id ? { id: draft.id } : {}),
        mappingType: 'direct',
        sourceFieldPath: srcPath,
        destinationFieldPath: dstPath,
        ordinalPosition,
        metadata: draft.metadata ?? {},
      },
    }
  }

  if (draft.mappingType === 'constant') {
    const parsedConstant = parseConstantJsonInput(draft.constantJsonText)
    if (!parsedConstant.ok) {
      return { ok: false, error: parsedConstant.error }
    }
    return {
      ok: true,
      rule: {
        ...(draft.id ? { id: draft.id } : {}),
        mappingType: 'constant',
        destinationFieldPath: dstPath,
        constantValue: parsedConstant.value,
        ordinalPosition,
        metadata: draft.metadata ?? {},
      },
    }
  }

  // Structural transform reference
  const srcPath = draft.sourceFieldPath.trim()
  if (!srcPath) {
    return {
      ok: false,
      error: `Transform reference targeting '${dstPath}' requires a source field.`,
    }
  }
  const preset = draft.transformPresetName.trim()
  const transformId = draft.transformId.trim()
  if (!preset && !transformId) {
    return {
      ok: false,
      error: `Transform reference targeting '${dstPath}' requires a preset name or transform ID.`,
    }
  }
  if (preset && !IDENTIFIER_PATTERN.test(preset)) {
    return {
      ok: false,
      error:
        'Transform preset name may only contain letters, digits, _, -, ., and :.',
    }
  }
  if (transformId && !IDENTIFIER_PATTERN.test(transformId)) {
    return {
      ok: false,
      error:
        'Transform ID may only contain letters, digits, _, -, ., and :.',
    }
  }
  const paramsRes = serializeTransformParams(draft.transformParams)
  if (!paramsRes.ok) {
    return { ok: false, error: paramsRes.error }
  }
  const transformRef: TransformReference = {
    ...(preset ? { presetName: preset } : {}),
    ...(transformId ? { transformId } : {}),
    params: paramsRes.params,
  }
  return {
    ok: true,
    rule: {
      ...(draft.id ? { id: draft.id } : {}),
      mappingType: 'transform',
      sourceFieldPath: srcPath,
      destinationFieldPath: dstPath,
      transformRef,
      ordinalPosition,
      metadata: draft.metadata ?? {},
    },
  }
}

export function serializeRuleDrafts(
  drafts: MappingRuleDraft[],
):
  | { ok: true; rules: MappingRuleInput[] }
  | { ok: false; error: string } {
  const seenDest = new Set<string>()
  const serialized: MappingRuleInput[] = []
  for (let idx = 0; idx < drafts.length; idx += 1) {
    const res = serializeRuleDraft(drafts[idx], idx)
    if (!res.ok) return res
    if (seenDest.has(res.rule.destinationFieldPath)) {
      return {
        ok: false,
        error: `Duplicate mapping rule for destination field '${res.rule.destinationFieldPath}'.`,
      }
    }
    seenDest.add(res.rule.destinationFieldPath)
    serialized.push(res.rule)
  }
  return { ok: true, rules: serialized }
}

export function reorderRuleDrafts(
  drafts: MappingRuleDraft[],
  fromIndex: number,
  toIndex: number,
): MappingRuleDraft[] {
  if (
    fromIndex < 0 ||
    toIndex < 0 ||
    fromIndex >= drafts.length ||
    toIndex >= drafts.length ||
    fromIndex === toIndex
  ) {
    return drafts
  }
  const next = [...drafts]
  const [moved] = next.splice(fromIndex, 1)
  next.splice(toIndex, 0, moved)
  return next.map((rule, index) => ({
    ...rule,
    ordinalPosition: index,
  }))
}

function normalizeRulesForComparison(rules: MappingRuleInput[]): string {
  return JSON.stringify(
    rules.map((r, idx) => ({
      sourceFieldPath: r.sourceFieldPath ?? null,
      destinationFieldPath: r.destinationFieldPath,
      mappingType: r.mappingType,
      constantValue: r.mappingType === 'constant' ? (r.constantValue ?? null) : null,
      transformRef:
        r.mappingType === 'transform' && r.transformRef
          ? {
              transformId: r.transformRef.transformId ?? null,
              presetName: r.transformRef.presetName ?? null,
              params: Object.fromEntries(
                Object.entries(r.transformRef.params ?? {}).sort(([a], [b]) =>
                  a.localeCompare(b),
                ),
              ),
            }
          : null,
      ordinalPosition: idx,
    })),
  )
}

export function buildPartialUpdatePayload(
  existing: MappingRecord,
  form: MappingEditorFormState,
  serializedRules: MappingRuleInput[],
): UpdateMappingPayload {
  const payload: UpdateMappingPayload = {}
  const nextName = form.name.trim()
  if (nextName !== existing.name) {
    payload.name = nextName
  }

  const nextDesc = form.description.trim() || null
  const prevDesc = existing.description?.trim() || null
  if (nextDesc !== prevDesc) {
    payload.description = nextDesc
  }

  if (form.sourceConnectionId !== existing.sourceConnectionId) {
    payload.sourceConnectionId = form.sourceConnectionId
  }
  if (form.sourceObjectKey !== existing.sourceObjectKey) {
    payload.sourceObjectKey = form.sourceObjectKey
  }
  if (form.destinationConnectionId !== existing.destinationConnectionId) {
    payload.destinationConnectionId = form.destinationConnectionId
  }
  if (form.destinationObjectKey !== existing.destinationObjectKey) {
    payload.destinationObjectKey = form.destinationObjectKey
  }

  if (
    form.sourceSchemaFingerprint &&
    form.sourceSchemaFingerprint !== existing.sourceSchemaFingerprint
  ) {
    payload.sourceSchemaFingerprint = form.sourceSchemaFingerprint
  }
  if (
    form.destinationSchemaFingerprint &&
    form.destinationSchemaFingerprint !== existing.destinationSchemaFingerprint
  ) {
    payload.destinationSchemaFingerprint = form.destinationSchemaFingerprint
  }

  const existingRuleInputs: MappingRuleInput[] = [...existing.rules]
    .sort((a, b) => a.ordinalPosition - b.ordinalPosition)
    .map((rule) => ({
      id: rule.id,
      mappingType: rule.mappingType,
      sourceFieldPath: rule.sourceFieldPath,
      destinationFieldPath: rule.destinationFieldPath,
      ...(rule.mappingType === 'constant'
        ? { constantValue: rule.constantValue }
        : {}),
      ...(rule.mappingType === 'transform'
        ? { transformRef: rule.transformRef }
        : {}),
      ordinalPosition: rule.ordinalPosition,
    }))

  if (
    normalizeRulesForComparison(serializedRules) !==
    normalizeRulesForComparison(existingRuleInputs)
  ) {
    payload.rules = serializedRules
  }

  return payload
}

export function formatMappingTimestamp(isoString: string): string {
  const date = new Date(isoString)
  if (Number.isNaN(date.getTime())) return isoString
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
