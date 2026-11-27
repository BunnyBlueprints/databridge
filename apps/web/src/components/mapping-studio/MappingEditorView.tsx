import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  AlertTriangle,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  CheckCircle2,
  Compass,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  createMapping,
  getMapping,
  MappingApiClientError,
  updateMapping,
  validateMapping,
} from '../../services/mappingService.ts'
import {
  getConnectionSchema,
  getSchemaObjectFields,
  SchemaApiClientError,
} from '../../services/schemaService.ts'
import type { Connection } from '../../types/connection.ts'
import type {
  MappingRecord,
  MappingRuleType,
  MappingStatus,
  MappingValidationSummary,
} from '../../types/mapping.ts'
import type { SchemaField, SchemaObjectSummary } from '../../types/schema.ts'
import { Badge } from '../ui/Badge.tsx'
import { Button } from '../ui/Button.tsx'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/Card.tsx'
import { ConnectorIcon } from '../ui/ConnectorIcon.tsx'
import {
  buildPartialUpdatePayload,
  createLocalRuleKey,
  getMappingStatusDisplay,
  isEffectivelyRequiredDestinationField,
  parseConstantJsonInput,
  reorderRuleDrafts,
  ruleResponseToDraft,
  serializeRuleDraft,
  serializeRuleDrafts,
  type MappingEditorFormState,
  type MappingRuleDraft,
  type TransformParamDraft,
} from './mappingHelpers.ts'
import { ValidationSummaryPanel } from './ValidationSummaryPanel.tsx'

interface SideSchemaState {
  isLoadingObjects: boolean
  objectsError: string | null
  schemaNotDiscovered: boolean
  schemaFingerprint: string | null
  objects: SchemaObjectSummary[]
  isLoadingFields: boolean
  fieldsError: string | null
  fields: SchemaField[]
}

const EMPTY_SIDE_STATE: SideSchemaState = {
  isLoadingObjects: false,
  objectsError: null,
  schemaNotDiscovered: false,
  schemaFingerprint: null,
  objects: [],
  isLoadingFields: false,
  fieldsError: null,
  fields: [],
}

interface MappingEditorViewProps {
  connections: Connection[]
  initialMapping: MappingRecord | null
  onBackToList: () => void
  onMappingSaved: (record: MappingRecord) => void
  onMappingDeletedOrMissing?: () => void
}

function recordToFormState(
  record: MappingRecord | null,
  defaultSrcConnId: string,
  defaultDstConnId: string,
): MappingEditorFormState {
  if (!record) {
    return {
      name: '',
      description: '',
      sourceConnectionId: defaultSrcConnId,
      sourceObjectKey: '',
      sourceSchemaFingerprint: null,
      destinationConnectionId: defaultDstConnId,
      destinationObjectKey: '',
      destinationSchemaFingerprint: null,
      rules: [],
    }
  }
  return {
    name: record.name,
    description: record.description ?? '',
    sourceConnectionId: record.sourceConnectionId,
    sourceObjectKey: record.sourceObjectKey,
    sourceSchemaFingerprint: record.sourceSchemaFingerprint,
    destinationConnectionId: record.destinationConnectionId,
    destinationObjectKey: record.destinationObjectKey,
    destinationSchemaFingerprint: record.destinationSchemaFingerprint,
    rules: [...record.rules]
      .sort((a, b) => a.ordinalPosition - b.ordinalPosition)
      .map(ruleResponseToDraft),
  }
}

export function MappingEditorView({
  connections,
  initialMapping,
  onBackToList,
  onMappingSaved,
}: MappingEditorViewProps) {
  const [savedMapping, setSavedMapping] = useState<MappingRecord | null>(
    initialMapping,
  )
  const [form, setForm] = useState<MappingEditorFormState>(() =>
    recordToFormState(initialMapping, '', ''),
  )
  const [status, setStatus] = useState<MappingStatus>(
    initialMapping?.status ?? 'draft',
  )
  const [validationSummary, setValidationSummary] =
    useState<MappingValidationSummary | null>(
      initialMapping?.validationSummary ?? null,
    )

  const [sourceSide, setSourceSide] =
    useState<SideSchemaState>(EMPTY_SIDE_STATE)
  const [destSide, setDestSide] = useState<SideSchemaState>(EMPTY_SIDE_STATE)

  // Rule composer state
  const [editingRuleKey, setEditingRuleKey] = useState<string | null>(null)
  const [composerType, setComposerType] = useState<MappingRuleType>('direct')
  const [composerSourceField, setComposerSourceField] = useState('')
  const [composerDestField, setComposerDestField] = useState('')
  const [composerConstantText, setComposerConstantText] = useState('"value"')
  const [composerPresetName, setComposerPresetName] = useState('')
  const [composerTransformId, setComposerTransformId] = useState('')
  const [composerParams, setComposerParams] = useState<TransformParamDraft[]>(
    [],
  )
  const [composerError, setComposerError] = useState<string | null>(null)

  const [objectChangeNotice, setObjectChangeNotice] = useState<string | null>(
    null,
  )
  const [isSaving, setIsSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveNotice, setSaveNotice] = useState<string | null>(null)
  const [isValidating, setIsValidating] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [versionConflictError, setVersionConflictError] = useState<
    string | null
  >(null)
  const [isReloadingLatest, setIsReloadingLatest] = useState(false)

  const sourceAbortRef = useRef<AbortController | null>(null)
  const destAbortRef = useRef<AbortController | null>(null)
  const sourceFieldsAbortRef = useRef<AbortController | null>(null)
  const destFieldsAbortRef = useRef<AbortController | null>(null)
  const sourceEpochRef = useRef(0)
  const destEpochRef = useRef(0)

  const loadFieldsForObject = useCallback(
    async (
      side: 'source' | 'destination',
      connectionId: string,
      object: SchemaObjectSummary,
      epoch: number,
    ) => {
      const abortRef =
        side === 'source' ? sourceFieldsAbortRef : destFieldsAbortRef
      const setSide = side === 'source' ? setSourceSide : setDestSide
      const epochRef = side === 'source' ? sourceEpochRef : destEpochRef

      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      setSide((prev) => ({
        ...prev,
        isLoadingFields: true,
        fieldsError: null,
        fields: [],
      }))

      try {
        const res = await getSchemaObjectFields(
          connectionId,
          object.id,
          { limit: 500, offset: 0 },
          { signal: controller.signal },
        )
        if (controller.signal.aborted || epoch !== epochRef.current) return
        setSide((prev) => ({
          ...prev,
          isLoadingFields: false,
          fieldsError: null,
          fields: res.fields,
        }))
      } catch (err) {
        if (err instanceof SchemaApiClientError && err.isAborted) return
        if (epoch !== epochRef.current) return
        setSide((prev) => ({
          ...prev,
          isLoadingFields: false,
          fieldsError:
            err instanceof Error
              ? err.message
              : 'Unable to load object fields.',
        }))
      }
    },
    [],
  )

  const loadSideSchema = useCallback(
    async (
      side: 'source' | 'destination',
      connectionId: string,
      desiredObjectKey: string,
    ) => {
      const schemaAbortRef = side === 'source' ? sourceAbortRef : destAbortRef
      const fieldsAbortRef =
        side === 'source' ? sourceFieldsAbortRef : destFieldsAbortRef
      const setSide = side === 'source' ? setSourceSide : setDestSide
      const epochRef = side === 'source' ? sourceEpochRef : destEpochRef

      schemaAbortRef.current?.abort()
      fieldsAbortRef.current?.abort()
      epochRef.current += 1
      const currentEpoch = epochRef.current

      if (!connectionId) {
        setSide(EMPTY_SIDE_STATE)
        return
      }

      const controller = new AbortController()
      schemaAbortRef.current = controller

      setSide({
        ...EMPTY_SIDE_STATE,
        isLoadingObjects: true,
      })

      try {
        const schema = await getConnectionSchema(connectionId, {
          signal: controller.signal,
        })
        if (controller.signal.aborted || currentEpoch !== epochRef.current) {
          return
        }

        const matchedObject = desiredObjectKey
          ? (schema.objects.find((o) => o.objectKey === desiredObjectKey) ??
            null)
          : null

        setSide({
          isLoadingObjects: false,
          objectsError: null,
          schemaNotDiscovered: false,
          schemaFingerprint: schema.schemaFingerprint,
          objects: schema.objects,
          isLoadingFields: false,
          fieldsError: null,
          fields: [],
        })

        setForm((prev) => {
          if (side === 'source') {
            return {
              ...prev,
              sourceSchemaFingerprint:
                prev.sourceSchemaFingerprint ?? schema.schemaFingerprint,
            }
          }
          return {
            ...prev,
            destinationSchemaFingerprint:
              prev.destinationSchemaFingerprint ?? schema.schemaFingerprint,
          }
        })

        if (matchedObject) {
          void loadFieldsForObject(
            side,
            connectionId,
            matchedObject,
            currentEpoch,
          )
        }
      } catch (err) {
        if (err instanceof SchemaApiClientError && err.isAborted) return
        if (currentEpoch !== epochRef.current) return
        const isUndiscovered =
          err instanceof SchemaApiClientError &&
          (err.errorCode === 'SCHEMA_NOT_DISCOVERED' || err.status === 404)
        setSide({
          ...EMPTY_SIDE_STATE,
          schemaNotDiscovered: isUndiscovered,
          objectsError: isUndiscovered
            ? null
            : err instanceof Error
              ? err.message
              : 'Unable to load connection schema.',
        })
      }
    },
    [loadFieldsForObject],
  )

  useEffect(() => {
    void loadSideSchema(
      'source',
      form.sourceConnectionId,
      form.sourceObjectKey,
    )
    // Only run on sourceConnectionId change; object changes invoke loadFieldsForObject directly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.sourceConnectionId])

  useEffect(() => {
    void loadSideSchema(
      'destination',
      form.destinationConnectionId,
      form.destinationObjectKey,
    )
    // Only run on destinationConnectionId change; object changes invoke loadFieldsForObject directly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.destinationConnectionId])

  const abortAllSideRequests = useCallback(() => {
    sourceAbortRef.current?.abort()
    destAbortRef.current?.abort()
    sourceFieldsAbortRef.current?.abort()
    destFieldsAbortRef.current?.abort()
  }, [])

  useEffect(() => {
    return () => {
      abortAllSideRequests()
    }
  }, [abortAllSideRequests])

  const resetComposer = useCallback(() => {
    setEditingRuleKey(null)
    setComposerSourceField('')
    setComposerDestField('')
    setComposerConstantText('"value"')
    setComposerPresetName('')
    setComposerTransformId('')
    setComposerParams([])
    setComposerError(null)
  }, [])

  const handleSideConnectionChange = (
    side: 'source' | 'destination',
    nextConnectionId: string,
  ) => {
    const hadRules = form.rules.length > 0
    setSaveError(null)
    setSaveNotice(null)
    setValidationError(null)
    resetComposer()
    setForm((prev) => {
      if (side === 'source') {
        return {
          ...prev,
          sourceConnectionId: nextConnectionId,
          sourceObjectKey: '',
          sourceSchemaFingerprint: null,
          rules: [],
        }
      }
      return {
        ...prev,
        destinationConnectionId: nextConnectionId,
        destinationObjectKey: '',
        destinationSchemaFingerprint: null,
        rules: [],
      }
    })
    if (hadRules) {
      setObjectChangeNotice(
        `Cleared previous rules because the ${side} connection changed.`,
      )
    }
  }

  const handleSideObjectChange = (
    side: 'source' | 'destination',
    nextObjectKey: string,
  ) => {
    const sideState = side === 'source' ? sourceSide : destSide
    const epochRef = side === 'source' ? sourceEpochRef : destEpochRef
    const connId =
      side === 'source'
        ? form.sourceConnectionId
        : form.destinationConnectionId

    const hadRules = form.rules.length > 0
    setSaveError(null)
    setSaveNotice(null)
    setValidationError(null)
    resetComposer()

    setForm((prev) => {
      if (side === 'source') {
        return {
          ...prev,
          sourceObjectKey: nextObjectKey,
          sourceSchemaFingerprint:
            sideState.schemaFingerprint ?? prev.sourceSchemaFingerprint,
          rules: [],
        }
      }
      return {
        ...prev,
        destinationObjectKey: nextObjectKey,
        destinationSchemaFingerprint:
          sideState.schemaFingerprint ?? prev.destinationSchemaFingerprint,
        rules: [],
      }
    })

    if (hadRules) {
      setObjectChangeNotice(
        `Cleared previous rules because the ${side} object selection changed.`,
      )
    }

    const matched = sideState.objects.find(
      (o) => o.objectKey === nextObjectKey,
    )
    if (matched && connId) {
      void loadFieldsForObject(side, connId, matched, epochRef.current)
    } else if (side === 'source') {
      sourceFieldsAbortRef.current?.abort()
      setSourceSide((prev) => ({
        ...prev,
        isLoadingFields: false,
        fieldsError: null,
        fields: [],
      }))
    } else {
      destFieldsAbortRef.current?.abort()
      setDestSide((prev) => ({
        ...prev,
        isLoadingFields: false,
        fieldsError: null,
        fields: [],
      }))
    }
  }

  const sourceObjectMatch = useMemo(
    () =>
      sourceSide.objects.find((o) => o.objectKey === form.sourceObjectKey) ??
      null,
    [sourceSide.objects, form.sourceObjectKey],
  )

  const destObjectMatch = useMemo(
    () =>
      destSide.objects.find((o) => o.objectKey === form.destinationObjectKey) ??
      null,
    [destSide.objects, form.destinationObjectKey],
  )

  const ruleByDestField = useMemo(() => {
    const map = new Map<string, MappingRuleDraft>()
    for (const rule of form.rules) {
      map.set(rule.destinationFieldPath, rule)
    }
    return map
  }, [form.rules])

  const ruleCountBySourceField = useMemo(() => {
    const map = new Map<string, number>()
    for (const rule of form.rules) {
      if (rule.sourceFieldPath) {
        map.set(rule.sourceFieldPath, (map.get(rule.sourceFieldPath) ?? 0) + 1)
      }
    }
    return map
  }, [form.rules])

  const ruleSerialization = useMemo(
    () => serializeRuleDrafts(form.rules),
    [form.rules],
  )

  const partialUpdatePayload = useMemo(() => {
    if (!savedMapping || !ruleSerialization.ok) return null
    return buildPartialUpdatePayload(savedMapping, form, ruleSerialization.rules)
  }, [savedMapping, form, ruleSerialization])

  const hasUnsavedChanges = useMemo(() => {
    if (!savedMapping) return true
    if (!partialUpdatePayload) return true
    return Object.keys(partialUpdatePayload).length > 0
  }, [savedMapping, partialUpdatePayload])

  const constantPreviewCheck = useMemo(() => {
    if (composerType !== 'constant') return null
    return parseConstantJsonInput(composerConstantText)
  }, [composerType, composerConstantText])

  const handleStartEditRule = (rule: MappingRuleDraft) => {
    setEditingRuleKey(rule.localKey)
    setComposerType(rule.mappingType)
    setComposerSourceField(rule.sourceFieldPath)
    setComposerDestField(rule.destinationFieldPath)
    setComposerConstantText(rule.constantJsonText)
    setComposerPresetName(rule.transformPresetName)
    setComposerTransformId(rule.transformId)
    setComposerParams(rule.transformParams)
    setComposerError(null)
  }

  const handleSubmitComposer = () => {
    setComposerError(null)
    const dstPath = composerDestField.trim()
    if (!dstPath) {
      setComposerError('Choose a destination field for this mapping rule.')
      return
    }

    const existingDuplicate = form.rules.find(
      (r) =>
        r.destinationFieldPath === dstPath && r.localKey !== editingRuleKey,
    )
    if (existingDuplicate) {
      setComposerError(
        `Destination field '${dstPath}' already has a mapping rule. Edit or remove that rule first.`,
      )
      return
    }

    const nextDraft: MappingRuleDraft = {
      localKey: editingRuleKey ?? createLocalRuleKey(),
      id:
        editingRuleKey !== null
          ? (form.rules.find((r) => r.localKey === editingRuleKey)?.id ?? null)
          : null,
      mappingType: composerType,
      sourceFieldPath:
        composerType === 'constant' ? '' : composerSourceField.trim(),
      destinationFieldPath: dstPath,
      constantJsonText:
        composerType === 'constant' ? composerConstantText : '"value"',
      transformPresetName:
        composerType === 'transform' ? composerPresetName.trim() : '',
      transformId:
        composerType === 'transform' ? composerTransformId.trim() : '',
      transformParams: composerType === 'transform' ? composerParams : [],
      ordinalPosition:
        editingRuleKey !== null
          ? (form.rules.find((r) => r.localKey === editingRuleKey)
              ?.ordinalPosition ?? form.rules.length)
          : form.rules.length,
      metadata:
        editingRuleKey !== null
          ? (form.rules.find((r) => r.localKey === editingRuleKey)?.metadata ??
            {})
          : {},
    }

    const check = serializeRuleDraft(nextDraft, nextDraft.ordinalPosition)
    if (!check.ok) {
      setComposerError(check.error)
      return
    }

    setSaveError(null)
    setSaveNotice(null)
    setForm((prev) => {
      if (editingRuleKey !== null) {
        return {
          ...prev,
          sourceSchemaFingerprint:
            sourceSide.schemaFingerprint ?? prev.sourceSchemaFingerprint,
          destinationSchemaFingerprint:
            destSide.schemaFingerprint ?? prev.destinationSchemaFingerprint,
          rules: prev.rules.map((r, idx) =>
            r.localKey === editingRuleKey
              ? { ...nextDraft, ordinalPosition: idx }
              : r,
          ),
        }
      }
      return {
        ...prev,
        sourceSchemaFingerprint:
          sourceSide.schemaFingerprint ?? prev.sourceSchemaFingerprint,
        destinationSchemaFingerprint:
          destSide.schemaFingerprint ?? prev.destinationSchemaFingerprint,
        rules: [
          ...prev.rules,
          { ...nextDraft, ordinalPosition: prev.rules.length },
        ],
      }
    })
    resetComposer()
  }

  const handleRemoveRule = (localKey: string) => {
    if (editingRuleKey === localKey) resetComposer()
    setForm((prev) => ({
      ...prev,
      sourceSchemaFingerprint:
        sourceSide.schemaFingerprint ?? prev.sourceSchemaFingerprint,
      destinationSchemaFingerprint:
        destSide.schemaFingerprint ?? prev.destinationSchemaFingerprint,
      rules: prev.rules
        .filter((r) => r.localKey !== localKey)
        .map((r, idx) => ({ ...r, ordinalPosition: idx })),
    }))
  }

  const handleMoveRule = (index: number, delta: -1 | 1) => {
    setForm((prev) => ({
      ...prev,
      sourceSchemaFingerprint:
        sourceSide.schemaFingerprint ?? prev.sourceSchemaFingerprint,
      destinationSchemaFingerprint:
        destSide.schemaFingerprint ?? prev.destinationSchemaFingerprint,
      rules: reorderRuleDrafts(prev.rules, index, index + delta),
    }))
  }

  const handleSave = async () => {
    setSaveError(null)
    setSaveNotice(null)
    setValidationError(null)
    setVersionConflictError(null)

    const trimmedName = form.name.trim()
    if (!trimmedName) {
      setSaveError('Enter a mapping name before saving.')
      return
    }
    if (!sourceObjectMatch || !destObjectMatch) {
      setSaveError(
        'Select discovered source and destination objects before saving.',
      )
      return
    }
    if (!ruleSerialization.ok) {
      setSaveError(ruleSerialization.error)
      return
    }

    setIsSaving(true)
    try {
      let persisted: MappingRecord
      if (!savedMapping) {
        persisted = await createMapping({
          name: trimmedName,
          description: form.description.trim() || null,
          sourceConnectionId: form.sourceConnectionId,
          sourceObjectKey: form.sourceObjectKey,
          destinationConnectionId: form.destinationConnectionId,
          destinationObjectKey: form.destinationObjectKey,
          ...(sourceSide.schemaFingerprint
            ? { sourceSchemaFingerprint: sourceSide.schemaFingerprint }
            : {}),
          ...(destSide.schemaFingerprint
            ? { destinationSchemaFingerprint: destSide.schemaFingerprint }
            : {}),
          rules: ruleSerialization.rules,
        })
      } else {
        const diff = buildPartialUpdatePayload(
          savedMapping,
          form,
          ruleSerialization.rules,
        )
        if (Object.keys(diff).length === 0) {
          setSaveNotice('Mapping is already up to date.')
          setIsSaving(false)
          return
        }
        persisted = await updateMapping(savedMapping.id, {
          ...diff,
          expectedVersion: savedMapping.mappingVersion,
        })
      }

      setSavedMapping(persisted)
      setForm(
        recordToFormState(
          persisted,
          persisted.sourceConnectionId,
          persisted.destinationConnectionId,
        ),
      )
      setStatus(persisted.status)
      setValidationSummary(persisted.validationSummary)
      setObjectChangeNotice(null)
      setSaveNotice(
        `Saved mapping v${persisted.mappingVersion} as Draft. Run Validate Mapping to verify against backend schemas.`,
      )
      onMappingSaved(persisted)
    } catch (err) {
      if (
        err instanceof MappingApiClientError &&
        err.errorCode === 'MAPPING_VERSION_CONFLICT'
      ) {
        setVersionConflictError(err.message)
      } else {
        setSaveError(
          err instanceof Error ? err.message : 'Unable to save mapping.',
        )
      }
    } finally {
      setIsSaving(false)
    }
  }

  const handleValidate = async () => {
    if (!savedMapping) return
    setValidationError(null)
    setVersionConflictError(null)
    setSaveNotice(null)
    setIsValidating(true)
    try {
      const outcome = await validateMapping(savedMapping.id)
      // Refresh full mapping detail from backend after validation.
      const refreshed = await getMapping(savedMapping.id)
      setSavedMapping(refreshed)
      setStatus(outcome.status)
      setValidationSummary(outcome.validationSummary)
      onMappingSaved(refreshed)
    } catch (err) {
      if (
        err instanceof MappingApiClientError &&
        err.errorCode === 'MAPPING_VERSION_CONFLICT'
      ) {
        setVersionConflictError(err.message)
      } else {
        setValidationError(
          err instanceof Error ? err.message : 'Unable to validate mapping.',
        )
      }
    } finally {
      setIsValidating(false)
    }
  }

  const handleReloadLatest = async () => {
    if (!savedMapping) return
    setIsReloadingLatest(true)
    setVersionConflictError(null)
    setSaveError(null)
    setValidationError(null)
    try {
      const latest = await getMapping(savedMapping.id)
      setSavedMapping(latest)
      setForm(
        recordToFormState(
          latest,
          latest.sourceConnectionId,
          latest.destinationConnectionId,
        ),
      )
      setStatus(latest.status)
      setValidationSummary(latest.validationSummary)
      resetComposer()
      onMappingSaved(latest)
    } catch (err) {
      setSaveError(
        err instanceof Error
          ? err.message
          : 'Unable to reload the latest mapping record.',
      )
    } finally {
      setIsReloadingLatest(false)
    }
  }

  const statusDisplay = getMappingStatusDisplay(status)
  const hasPendingSnapshotSync = Boolean(
    savedMapping &&
      ((sourceSide.schemaFingerprint &&
        sourceSide.schemaFingerprint !== form.sourceSchemaFingerprint) ||
        (destSide.schemaFingerprint &&
          destSide.schemaFingerprint !== form.destinationSchemaFingerprint)),
  )

  const handleSyncSchemaSnapshots = () => {
    setSaveError(null)
    setSaveNotice(null)
    setForm((prev) => ({
      ...prev,
      sourceSchemaFingerprint:
        sourceSide.schemaFingerprint ?? prev.sourceSchemaFingerprint,
      destinationSchemaFingerprint:
        destSide.schemaFingerprint ?? prev.destinationSchemaFingerprint,
    }))
  }

  const canSave =
    Boolean(form.name.trim()) &&
    Boolean(sourceObjectMatch) &&
    Boolean(destObjectMatch) &&
    ruleSerialization.ok &&
    !isSaving

  const canValidate =
    Boolean(savedMapping) && !hasUnsavedChanges && !isSaving && !isValidating

  const srcConn = connections.find((c) => c.id === form.sourceConnectionId)
  const dstConn = connections.find(
    (c) => c.id === form.destinationConnectionId,
  )

  return (
    <div className="space-y-5">
      {/* Header bar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <Button
            variant="secondary"
            size="sm"
            onClick={onBackToList}
            className="dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Back to Mappings</span>
          </Button>

          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-lg font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
                {savedMapping ? savedMapping.name : 'Create Field Mapping'}
              </h1>
              <Badge tone={statusDisplay.tone} dot>
                {statusDisplay.label}
              </Badge>
              {savedMapping && (
                <Badge tone="zinc">v{savedMapping.mappingVersion}</Badge>
              )}
              {hasUnsavedChanges && savedMapping && (
                <Badge tone="amber">Unsaved edits</Badge>
              )}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            disabled={!canSave}
            onClick={() => void handleSave()}
          >
            {isSaving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            <span>{savedMapping ? 'Save Mapping' : 'Create Mapping'}</span>
          </Button>

          <Button
            variant="secondary"
            size="sm"
            disabled={!canValidate}
            onClick={() => void handleValidate()}
            className="dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
          >
            {isValidating ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <ShieldCheck
                className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400"
                aria-hidden="true"
              />
            )}
            <span>Validate Mapping</span>
          </Button>
        </div>
      </div>

      {/* Feedback / Conflict banners */}
      {versionConflictError && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-rose-200 bg-rose-50/90 px-4 py-3 text-xs text-rose-900 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-100"
        >
          <div className="flex items-start gap-2">
            <AlertCircle
              className="mt-0.5 h-4 w-4 shrink-0 text-rose-600 dark:text-rose-400"
              aria-hidden="true"
            />
            <div>
              <p className="font-semibold">Version Conflict</p>
              <p className="mt-0.5">{versionConflictError}</p>
            </div>
          </div>
          <Button
            variant="secondary"
            size="sm"
            disabled={isReloadingLatest}
            onClick={() => void handleReloadLatest()}
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${isReloadingLatest ? 'animate-spin' : ''}`}
              aria-hidden="true"
            />
            <span>Reload Latest Mapping</span>
          </Button>
        </div>
      )}

      {(saveError || validationError) && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-rose-200 bg-rose-50/80 px-3.5 py-2.5 text-xs text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-200"
        >
          <AlertCircle
            className="mt-0.5 h-4 w-4 shrink-0 text-rose-600 dark:text-rose-400"
            aria-hidden="true"
          />
          <span>{saveError ?? validationError}</span>
        </div>
      )}

      {saveNotice && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-md border border-emerald-200 bg-emerald-50/80 px-3.5 py-2.5 text-xs text-emerald-800 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-200"
        >
          <CheckCircle2
            className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400"
            aria-hidden="true"
          />
          <span>{saveNotice}</span>
        </div>
      )}

      {objectChangeNotice && (
        <div
          role="status"
          className="flex items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50/80 px-3.5 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
        >
          <span>{objectChangeNotice}</span>
          <button
            type="button"
            onClick={() => setObjectChangeNotice(null)}
            className="cursor-pointer font-medium underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {hasPendingSnapshotSync && (
        <div
          role="status"
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50/80 px-3.5 py-2.5 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
        >
          <span>
            Live schema snapshots differ from this mapping&apos;s stored
            fingerprints. Review your rules and apply the latest schema
            references before saving.
          </span>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleSyncSchemaSnapshots}
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Update Snapshot References</span>
          </Button>
        </div>
      )}

      {/* Mapping Metadata */}
      <Card className="dark:border-zinc-800 dark:bg-zinc-900">
        <CardContent className="grid grid-cols-1 gap-4 p-4 md:grid-cols-2">
          <div>
            <label
              htmlFor="mapping-name-input"
              className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
            >
              Mapping Name <span className="text-rose-500">*</span>
            </label>
            <input
              id="mapping-name-input"
              type="text"
              maxLength={120}
              value={form.name}
              onChange={(e) => {
                setSaveError(null)
                setSaveNotice(null)
                setForm((prev) => ({ ...prev, name: e.target.value }))
              }}
              placeholder="e.g. Postgres Customers to CRM Contacts"
              className="mt-1 h-9 w-full rounded-md border border-zinc-200 bg-white px-3 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
            />
          </div>

          <div>
            <label
              htmlFor="mapping-description-input"
              className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
            >
              Description
            </label>
            <input
              id="mapping-description-input"
              type="text"
              maxLength={500}
              value={form.description}
              onChange={(e) => {
                setSaveError(null)
                setSaveNotice(null)
                setForm((prev) => ({ ...prev, description: e.target.value }))
              }}
              placeholder="Optional description of mapping purpose"
              className="mt-1 h-9 w-full rounded-md border border-zinc-200 bg-white px-3 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
            />
          </div>
        </CardContent>
      </Card>

      {/* Source and Destination Selectors */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {(
          [
            {
              side: 'source' as const,
              title: 'Source Connection & Object',
              connId: form.sourceConnectionId,
              objKey: form.sourceObjectKey,
              sideState: sourceSide,
              conn: srcConn,
            },
            {
              side: 'destination' as const,
              title: 'Destination Connection & Object',
              connId: form.destinationConnectionId,
              objKey: form.destinationObjectKey,
              sideState: destSide,
              conn: dstConn,
            },
          ] as const
        ).map(({ side, title, connId, objKey, sideState, conn }) => (
          <Card key={side} className="dark:border-zinc-800 dark:bg-zinc-900">
            <CardHeader className="px-4 py-3 dark:border-zinc-800">
              <div className="flex w-full items-center justify-between gap-2">
                <CardTitle className="flex items-center gap-2 text-sm dark:text-zinc-100">
                  {conn && <ConnectorIcon type={conn.type} size="sm" />}
                  <span>{title}</span>
                </CardTitle>
                {connId && (
                  <Link
                    to={`/data-explorer?connectionId=${encodeURIComponent(connId)}`}
                    className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700 hover:underline dark:text-emerald-400"
                  >
                    <Compass className="h-3 w-3" aria-hidden="true" />
                    <span>Data Explorer</span>
                  </Link>
                )}
              </div>
            </CardHeader>

            <CardContent className="space-y-3 p-4">
              <div>
                <label
                  htmlFor={`${side}-connection-select`}
                  className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Connection
                </label>
                <select
                  id={`${side}-connection-select`}
                  value={connId}
                  onChange={(e) =>
                    handleSideConnectionChange(side, e.target.value)
                  }
                  className="mt-1 h-9 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                >
                  <option value="">Select {side} connection…</option>
                  {connections.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} ({c.type})
                    </option>
                  ))}
                </select>
              </div>

              {sideState.isLoadingObjects ? (
                <div className="flex items-center gap-2 py-2 text-xs text-zinc-500">
                  <Loader2
                    className="h-3.5 w-3.5 animate-spin text-emerald-600"
                    aria-hidden="true"
                  />
                  <span>Loading discovered objects…</span>
                </div>
              ) : sideState.schemaNotDiscovered ? (
                <div className="rounded-md border border-amber-200 bg-amber-50/80 px-3 py-2.5 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
                  <p className="font-semibold">Schema not discovered yet</p>
                  <p className="mt-0.5 leading-relaxed">
                    Discover objects and fields for this connection in Data
                    Explorer before mapping.
                  </p>
                  <div className="mt-2 flex items-center gap-3">
                    <Link
                      to={`/data-explorer?connectionId=${encodeURIComponent(connId)}`}
                      className="inline-flex items-center gap-1 font-semibold text-emerald-700 underline dark:text-emerald-400"
                    >
                      <Compass className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>Discover in Data Explorer</span>
                    </Link>
                    <button
                      type="button"
                      onClick={() => void loadSideSchema(side, connId, objKey)}
                      className="cursor-pointer font-medium underline"
                    >
                      Reload
                    </button>
                  </div>
                </div>
              ) : sideState.objectsError ? (
                <div
                  role="alert"
                  className="flex items-center justify-between gap-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-200"
                >
                  <span>{sideState.objectsError}</span>
                  <button
                    type="button"
                    onClick={() => void loadSideSchema(side, connId, objKey)}
                    className="cursor-pointer font-semibold underline"
                  >
                    Retry
                  </button>
                </div>
              ) : connId ? (
                <div>
                  <label
                    htmlFor={`${side}-object-select`}
                    className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Discovered Object
                  </label>
                  <select
                    id={`${side}-object-select`}
                    value={objKey}
                    onChange={(e) =>
                      handleSideObjectChange(side, e.target.value)
                    }
                    className="mt-1 h-9 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                  >
                    <option value="">Select {side} object…</option>
                    {sideState.objects.map((obj) => (
                      <option key={obj.id} value={obj.objectKey}>
                        [{obj.objectType}] {obj.objectKey} ({obj.fieldCount}{' '}
                        fields)
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Field Browser + Rule Composer */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Source Fields */}
        <Card className="flex flex-col dark:border-zinc-800 dark:bg-zinc-900">
          <CardHeader className="px-4 py-3 dark:border-zinc-800">
            <div className="flex w-full items-center justify-between gap-2">
              <CardTitle className="text-sm dark:text-zinc-100">
                Source Fields{' '}
                {sourceObjectMatch ? `(${sourceSide.fields.length})` : ''}
              </CardTitle>
              {sourceObjectMatch && (
                <span className="font-mono text-[11px] text-zinc-500">
                  {sourceObjectMatch.objectKey}
                </span>
              )}
            </div>
          </CardHeader>
          <CardContent className="max-h-72 overflow-y-auto p-0">
            {!form.sourceObjectKey ? (
              <p className="p-4 text-xs text-zinc-500 dark:text-zinc-400">
                Select a source connection and object to inspect fields.
              </p>
            ) : sourceSide.isLoadingFields ? (
              <div className="flex items-center justify-center py-8 text-xs text-zinc-500">
                <Loader2
                  className="mr-2 h-4 w-4 animate-spin text-emerald-600"
                  aria-hidden="true"
                />
                <span>Loading source fields…</span>
              </div>
            ) : sourceSide.fieldsError ? (
              <div className="p-4 text-xs text-rose-600">
                {sourceSide.fieldsError}
              </div>
            ) : (
              <ul className="divide-y divide-zinc-200/70 dark:divide-zinc-800">
                {sourceSide.fields.map((field) => {
                  const usedCount =
                    ruleCountBySourceField.get(field.fieldPath) ?? 0
                  return (
                    <li
                      key={field.id}
                      className="flex items-center justify-between gap-2 px-4 py-2 text-xs hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40"
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-medium text-zinc-900 dark:text-zinc-100">
                            {field.fieldName}
                          </span>
                          <Badge tone="zinc">{field.canonicalType}</Badge>
                          <span className="font-mono text-[10px] text-zinc-500">
                            {field.nativeType}
                          </span>
                          {field.primaryKey && <Badge tone="slate">PK</Badge>}
                          {field.uniqueKey && <Badge tone="slate">Unique</Badge>}
                          {field.nullable && (
                            <span className="text-[10px] text-zinc-500">
                              nullable
                            </span>
                          )}
                          {usedCount > 0 && (
                            <Badge tone="emerald">Mapped ({usedCount})</Badge>
                          )}
                        </div>
                        <div className="truncate font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
                          {field.fieldPath}
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          if (composerType === 'constant') {
                            setComposerType('direct')
                          }
                          setComposerSourceField(field.fieldPath)
                        }}
                        className="h-7 shrink-0 px-2 text-[11px] text-emerald-700 dark:text-emerald-400"
                      >
                        Select
                      </Button>
                    </li>
                  )
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* Destination Fields */}
        <Card className="flex flex-col dark:border-zinc-800 dark:bg-zinc-900">
          <CardHeader className="px-4 py-3 dark:border-zinc-800">
            <div className="flex w-full items-center justify-between gap-2">
              <CardTitle className="text-sm dark:text-zinc-100">
                Destination Fields{' '}
                {destObjectMatch ? `(${destSide.fields.length})` : ''}
              </CardTitle>
              {destObjectMatch && (
                <span className="font-mono text-[11px] text-zinc-500">
                  {destObjectMatch.objectKey}
                </span>
              )}
            </div>
          </CardHeader>
          <CardContent className="max-h-72 overflow-y-auto p-0">
            {!form.destinationObjectKey ? (
              <p className="p-4 text-xs text-zinc-500 dark:text-zinc-400">
                Select a destination connection and object to inspect fields.
              </p>
            ) : destSide.isLoadingFields ? (
              <div className="flex items-center justify-center py-8 text-xs text-zinc-500">
                <Loader2
                  className="mr-2 h-4 w-4 animate-spin text-emerald-600"
                  aria-hidden="true"
                />
                <span>Loading destination fields…</span>
              </div>
            ) : destSide.fieldsError ? (
              <div className="p-4 text-xs text-rose-600">
                {destSide.fieldsError}
              </div>
            ) : (
              <ul className="divide-y divide-zinc-200/70 dark:divide-zinc-800">
                {destSide.fields.map((field) => {
                  const mappedRule = ruleByDestField.get(field.fieldPath)
                  const required =
                    isEffectivelyRequiredDestinationField(field)
                  const isGenerated = Boolean(
                    field.defaultMetadata?.isGenerated ||
                      field.metadata.isGenerated ||
                      field.metadata.isIdentity ||
                      field.metadata.autoIncrement,
                  )
                  const hasDefault = Boolean(
                    field.defaultMetadata?.hasDefault ||
                      field.metadata.hasDefault,
                  )

                  return (
                    <li
                      key={field.id}
                      className="flex items-center justify-between gap-2 px-4 py-2 text-xs hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40"
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-medium text-zinc-900 dark:text-zinc-100">
                            {field.fieldName}
                          </span>
                          <Badge tone="zinc">{field.canonicalType}</Badge>
                          <span className="font-mono text-[10px] text-zinc-500">
                            {field.nativeType}
                          </span>
                          {required && (
                            <Badge tone={mappedRule ? 'zinc' : 'amber'}>
                              Required
                            </Badge>
                          )}
                          {isGenerated && <Badge tone="slate">Generated</Badge>}
                          {hasDefault && !isGenerated && (
                            <Badge tone="slate">Default</Badge>
                          )}
                          {field.primaryKey && <Badge tone="slate">PK</Badge>}
                          {field.uniqueKey && <Badge tone="slate">Unique</Badge>}
                          {mappedRule && (
                            <Badge tone="emerald">
                              Rule #{mappedRule.ordinalPosition + 1}
                            </Badge>
                          )}
                        </div>
                        <div className="truncate font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
                          {field.fieldPath}
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          if (mappedRule) {
                            handleStartEditRule(mappedRule)
                          } else {
                            setComposerDestField(field.fieldPath)
                          }
                        }}
                        className="h-7 shrink-0 px-2 text-[11px] text-emerald-700 dark:text-emerald-400"
                      >
                        {mappedRule ? 'Edit Rule' : 'Target'}
                      </Button>
                    </li>
                  )
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Rule Composer */}
      <Card className="dark:border-zinc-800 dark:bg-zinc-900">
        <CardHeader className="px-4 py-3 dark:border-zinc-800">
          <div className="flex w-full items-center justify-between gap-2">
            <CardTitle className="flex items-center gap-2 text-sm dark:text-zinc-100">
              <Sparkles
                className="h-4 w-4 text-emerald-600 dark:text-emerald-400"
                aria-hidden="true"
              />
              <span>
                {editingRuleKey ? 'Edit Mapping Rule' : 'Add Mapping Rule'}
              </span>
            </CardTitle>
            {editingRuleKey && (
              <Button variant="ghost" size="sm" onClick={resetComposer}>
                Cancel edit
              </Button>
            )}
          </div>
        </CardHeader>

        <CardContent className="space-y-4 p-4">
          {composerError && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-md border border-rose-200 bg-rose-50/80 px-3 py-2 text-xs text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-200"
            >
              <AlertCircle
                className="mt-0.5 h-4 w-4 shrink-0 text-rose-600 dark:text-rose-400"
                aria-hidden="true"
              />
              <span>{composerError}</span>
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div>
              <label
                htmlFor="rule-type-select"
                className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
              >
                Rule Type
              </label>
              <select
                id="rule-type-select"
                value={composerType}
                onChange={(e) => {
                  setComposerError(null)
                  setComposerType(e.target.value as MappingRuleType)
                }}
                className="mt-1 h-9 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
              >
                <option value="direct">Direct field mapping</option>
                <option value="constant">Constant JSON value</option>
                <option value="transform">
                  Structural transform reference (not executed)
                </option>
              </select>
            </div>

            {composerType !== 'constant' && (
              <div>
                <label
                  htmlFor="rule-source-field-select"
                  className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Source Field
                </label>
                <select
                  id="rule-source-field-select"
                  value={composerSourceField}
                  onChange={(e) => {
                    setComposerError(null)
                    setComposerSourceField(e.target.value)
                  }}
                  className="mt-1 h-9 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                >
                  <option value="">Select source field…</option>
                  {sourceSide.fields.map((field) => (
                    <option key={field.id} value={field.fieldPath}>
                      {field.fieldPath} ({field.canonicalType})
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div>
              <label
                htmlFor="rule-dest-field-select"
                className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
              >
                Destination Field
              </label>
              <select
                id="rule-dest-field-select"
                value={composerDestField}
                onChange={(e) => {
                  setComposerError(null)
                  setComposerDestField(e.target.value)
                }}
                className="mt-1 h-9 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
              >
                <option value="">Select destination field…</option>
                {destSide.fields.map((field) => {
                  const alreadyMappedByOther = form.rules.some(
                    (r) =>
                      r.destinationFieldPath === field.fieldPath &&
                      r.localKey !== editingRuleKey,
                  )
                  return (
                    <option
                      key={field.id}
                      value={field.fieldPath}
                      disabled={alreadyMappedByOther}
                    >
                      {field.fieldPath} ({field.canonicalType})
                      {alreadyMappedByOther ? ' — mapped' : ''}
                    </option>
                  )
                })}
              </select>
            </div>
          </div>

          {composerType === 'constant' && (
            <div className="space-y-2 rounded-md border border-zinc-200/80 bg-zinc-50/70 p-3 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label
                  htmlFor="constant-json-input"
                  className="text-xs font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Constant JSON Value
                </label>
                <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                  <span className="text-zinc-500">Quick JSON:</span>
                  {[
                    { label: 'null', value: 'null' },
                    { label: '"text"', value: '"value"' },
                    { label: '0', value: '0' },
                    { label: 'true', value: 'true' },
                    { label: '[]', value: '[]' },
                    { label: '{}', value: '{}' },
                  ].map((preset) => (
                    <button
                      key={preset.label}
                      type="button"
                      onClick={() => {
                        setComposerError(null)
                        setComposerConstantText(preset.value)
                      }}
                      className="cursor-pointer rounded border border-zinc-200 bg-white px-2 py-0.5 font-mono text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>
              </div>
              <textarea
                id="constant-json-input"
                rows={2}
                value={composerConstantText}
                onChange={(e) => {
                  setComposerError(null)
                  setComposerConstantText(e.target.value)
                }}
                placeholder='"production" or 42 or true or null or {"key": "value"}'
                className="w-full rounded-md border border-zinc-200 bg-white px-3 py-2 font-mono text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
              />
              {constantPreviewCheck && !constantPreviewCheck.ok && (
                <p className="text-[11px] text-rose-600 dark:text-rose-400">
                  {constantPreviewCheck.error}
                </p>
              )}
            </div>
          )}

          {composerType === 'transform' && (
            <div className="space-y-3 rounded-md border border-zinc-200/80 bg-zinc-50/70 p-3 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div className="flex items-start gap-2 text-xs text-zinc-600 dark:text-zinc-300">
                <AlertTriangle
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500"
                  aria-hidden="true"
                />
                <span>
                  Structural transform reference only — DataBridge stores
                  preset/transform identifiers and scalar parameters without
                  executing code, SQL, or scripts.
                </span>
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="transform-preset-input"
                    className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Preset Name
                  </label>
                  <input
                    id="transform-preset-input"
                    type="text"
                    value={composerPresetName}
                    onChange={(e) => {
                      setComposerError(null)
                      setComposerPresetName(e.target.value)
                    }}
                    placeholder="e.g. lowercase_trim"
                    className="mt-1 h-8 w-full rounded-md border border-zinc-200 bg-white px-2.5 font-mono text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                  />
                </div>
                <div>
                  <label
                    htmlFor="transform-id-input"
                    className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Registered Transform ID (optional)
                  </label>
                  <input
                    id="transform-id-input"
                    type="text"
                    value={composerTransformId}
                    onChange={(e) => {
                      setComposerError(null)
                      setComposerTransformId(e.target.value)
                    }}
                    placeholder="e.g. normalize_email_v1"
                    className="mt-1 h-8 w-full rounded-md border border-zinc-200 bg-white px-2.5 font-mono text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
                    Scalar Transform Parameters (no code/expressions)
                  </span>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() =>
                      setComposerParams((prev) => [
                        ...prev,
                        { key: '', value: '', valueKind: 'string' },
                      ])
                    }
                    className="h-7 px-2 text-[11px]"
                  >
                    <Plus className="h-3 w-3" aria-hidden="true" />
                    <span>Add Parameter</span>
                  </Button>
                </div>

                {composerParams.map((param, idx) => (
                  <div
                    key={idx}
                    className="grid grid-cols-1 gap-2 sm:grid-cols-12"
                  >
                    <input
                      type="text"
                      aria-label={`Transform parameter ${idx + 1} key`}
                      placeholder="Parameter key"
                      value={param.key}
                      onChange={(e) =>
                        setComposerParams((prev) =>
                          prev.map((item, i) =>
                            i === idx ? { ...item, key: e.target.value } : item,
                          ),
                        )
                      }
                      className="h-8 rounded-md border border-zinc-200 bg-white px-2.5 font-mono text-xs sm:col-span-4 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                    />
                    <select
                      aria-label={`Transform parameter ${idx + 1} type`}
                      value={param.valueKind}
                      onChange={(e) =>
                        setComposerParams((prev) =>
                          prev.map((item, i) =>
                            i === idx
                              ? {
                                  ...item,
                                  valueKind: e.target
                                    .value as TransformParamDraft['valueKind'],
                                }
                              : item,
                          ),
                        )
                      }
                      className="h-8 rounded-md border border-zinc-200 bg-white px-2 text-xs sm:col-span-3 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                    >
                      <option value="string">string</option>
                      <option value="number">number</option>
                      <option value="boolean">boolean</option>
                      <option value="null">null</option>
                    </select>
                    <input
                      type="text"
                      aria-label={`Transform parameter ${idx + 1} value`}
                      placeholder={
                        param.valueKind === 'null' ? 'null' : 'Parameter value'
                      }
                      disabled={param.valueKind === 'null'}
                      value={param.valueKind === 'null' ? 'null' : param.value}
                      onChange={(e) =>
                        setComposerParams((prev) =>
                          prev.map((item, i) =>
                            i === idx
                              ? { ...item, value: e.target.value }
                              : item,
                          ),
                        )
                      }
                      className="h-8 rounded-md border border-zinc-200 bg-white px-2.5 text-xs sm:col-span-4 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                    />
                    <button
                      type="button"
                      aria-label={`Remove parameter ${idx + 1}`}
                      onClick={() =>
                        setComposerParams((prev) =>
                          prev.filter((_, i) => i !== idx),
                        )
                      }
                      className="inline-flex h-8 cursor-pointer items-center justify-center rounded-md border border-zinc-200 text-rose-600 hover:bg-rose-50 sm:col-span-1 dark:border-zinc-700 dark:hover:bg-rose-950/30"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex justify-end">
            <Button
              variant="primary"
              size="sm"
              disabled={!sourceObjectMatch || !destObjectMatch}
              onClick={handleSubmitComposer}
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{editingRuleKey ? 'Update Rule' : 'Add Rule'}</span>
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Ordered Rules List */}
      <Card className="dark:border-zinc-800 dark:bg-zinc-900">
        <CardHeader className="px-4 py-3 dark:border-zinc-800">
          <CardTitle className="text-sm dark:text-zinc-100">
            Ordered Mapping Rules ({form.rules.length})
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {form.rules.length === 0 ? (
            <p className="p-4 text-xs text-zinc-500 dark:text-zinc-400">
              No rules defined yet. Add direct, constant, or structural
              transform-reference rules above.
            </p>
          ) : (
            <ul className="divide-y divide-zinc-200/80 dark:divide-zinc-800">
              {form.rules.map((rule, index) => (
                <li
                  key={rule.localKey}
                  className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-xs hover:bg-zinc-50/70 dark:hover:bg-zinc-800/40"
                >
                  <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2.5">
                    <span className="inline-flex h-6 w-7 shrink-0 items-center justify-center rounded border border-zinc-200 bg-zinc-50 font-mono text-[11px] font-semibold text-zinc-700 tabular-nums dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300">
                      #{index + 1}
                    </span>

                    <Badge
                      tone={
                        rule.mappingType === 'transform'
                          ? 'slate'
                          : rule.mappingType === 'constant'
                            ? 'amber'
                            : 'emerald'
                      }
                    >
                      {rule.mappingType === 'transform'
                        ? 'Transform Ref'
                        : rule.mappingType === 'constant'
                          ? 'Constant'
                          : 'Direct'}
                    </Badge>

                    <div className="flex min-w-0 flex-wrap items-center gap-1.5 font-mono">
                      {rule.mappingType === 'constant' ? (
                        <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200">
                          {rule.constantJsonText}
                        </span>
                      ) : (
                        <span className="text-zinc-800 dark:text-zinc-200">
                          {rule.sourceFieldPath}
                        </span>
                      )}

                      {rule.mappingType === 'transform' && (
                        <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                          via{' '}
                          {rule.transformPresetName || rule.transformId}
                        </span>
                      )}

                      <ArrowRight
                        className="h-3.5 w-3.5 shrink-0 text-zinc-400"
                        aria-hidden="true"
                      />

                      <span className="font-semibold text-emerald-700 dark:text-emerald-400">
                        {rule.destinationFieldPath}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      disabled={index === 0}
                      onClick={() => handleMoveRule(index, -1)}
                      aria-label={`Move rule ${index + 1} up`}
                      className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded border border-zinc-200 text-zinc-600 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    >
                      <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      disabled={index === form.rules.length - 1}
                      onClick={() => handleMoveRule(index, 1)}
                      aria-label={`Move rule ${index + 1} down`}
                      className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded border border-zinc-200 text-zinc-600 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    >
                      <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleStartEditRule(rule)}
                      aria-label={`Edit rule ${index + 1}`}
                      className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded border border-zinc-200 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    >
                      <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleRemoveRule(rule.localKey)}
                      aria-label={`Remove rule ${index + 1}`}
                      className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded border border-zinc-200 text-rose-600 hover:bg-rose-50 dark:border-zinc-700 dark:text-rose-400 dark:hover:bg-rose-950/30"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* Authoritative Backend Validation Results */}
      <ValidationSummaryPanel
        status={status}
        validationSummary={validationSummary}
        hasUnsavedChanges={hasUnsavedChanges}
        isNewMapping={savedMapping === null}
        sourceConnectionId={form.sourceConnectionId}
        destinationConnectionId={form.destinationConnectionId}
      />
    </div>
  )
}
