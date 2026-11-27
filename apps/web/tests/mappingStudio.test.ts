import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import {
  buildPartialUpdatePayload,
  getMappingStatusDisplay,
  isEffectivelyRequiredDestinationField,
  parseConstantJsonInput,
  reorderRuleDrafts,
  ruleResponseToDraft,
  serializeRuleDrafts,
  type MappingEditorFormState,
} from '../src/components/mapping-studio/mappingHelpers.ts'
import {
  createMapping,
  deleteMapping,
  getMapping,
  listMappings,
  MappingApiClientError,
  updateMapping,
  validateMapping,
} from '../src/services/mappingService.ts'
import type { MappingRecord } from '../src/types/mapping.ts'
import type { SchemaField } from '../src/types/schema.ts'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const webSrcDir = path.resolve(__dirname, '..', 'src')

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const SRC_CONN_ID = '11111111-1111-4111-8111-111111111111'
const DST_CONN_ID = '22222222-2222-4222-8222-222222222222'
const MAP_ID = '33333333-3333-4333-8333-333333333333'

function makeSampleRecord(overrides: Partial<MappingRecord> = {}): MappingRecord {
  return {
    id: MAP_ID,
    name: 'Customers to CRM',
    description: 'Sync customer profiles',
    sourceConnectionId: SRC_CONN_ID,
    sourceObjectKey: 'public.customers',
    destinationConnectionId: DST_CONN_ID,
    destinationObjectKey: 'POST /v1/contacts',
    status: 'draft',
    mappingVersion: 1,
    sourceSchemaFingerprint: 'a'.repeat(64),
    destinationSchemaFingerprint: 'b'.repeat(64),
    validationSummary: null,
    rules: [
      {
        id: '44444444-4444-4444-8444-444444444441',
        mappingId: MAP_ID,
        sourceFieldPath: 'email',
        destinationFieldPath: 'body.email',
        mappingType: 'direct',
        constantValue: null,
        transformRef: null,
        ordinalPosition: 0,
        metadata: {},
        createdAt: '2026-11-27T17:00:00+00:00',
        updatedAt: '2026-11-27T17:00:00+00:00',
      },
      {
        id: '44444444-4444-4444-8444-444444444442',
        mappingId: MAP_ID,
        sourceFieldPath: null,
        destinationFieldPath: 'body.source_system',
        mappingType: 'constant',
        constantValue: null,
        transformRef: null,
        ordinalPosition: 1,
        metadata: {},
        createdAt: '2026-11-27T17:00:00+00:00',
        updatedAt: '2026-11-27T17:00:00+00:00',
      },
    ],
    createdAt: '2026-11-27T17:00:00+00:00',
    updatedAt: '2026-11-27T17:00:00+00:00',
    ...overrides,
  }
}

test('mappingService integrates CRUD, filters, pagination, and validation endpoints cleanly', async () => {
  const calls: Array<{ url: string; method: string; body?: string }> = []
  const sample = makeSampleRecord()

  const fetcher: typeof fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString()
    calls.push({
      url,
      method: init?.method ?? 'GET',
      body: typeof init?.body === 'string' ? init.body : undefined,
    })

    if (url.startsWith('/api/v1/mappings?') && init?.method === 'GET') {
      return jsonResponse({
        items: [sample],
        total: 1,
        limit: 20,
        offset: 0,
      })
    }
    if (url === '/api/v1/mappings' && init?.method === 'POST') {
      return jsonResponse(sample, 201)
    }
    if (url === `/api/v1/mappings/${MAP_ID}` && init?.method === 'GET') {
      return jsonResponse(sample)
    }
    if (url === `/api/v1/mappings/${MAP_ID}` && init?.method === 'PUT') {
      return jsonResponse(
        makeSampleRecord({ name: 'Updated Name', mappingVersion: 2 }),
      )
    }
    if (url === `/api/v1/mappings/${MAP_ID}` && init?.method === 'DELETE') {
      return new Response(null, { status: 204 })
    }
    if (
      url === `/api/v1/mappings/${MAP_ID}/validate` &&
      init?.method === 'POST'
    ) {
      return jsonResponse({
        mappingId: MAP_ID,
        mappingVersion: 2,
        status: 'invalid',
        validationSummary: {
          isValid: false,
          errorCount: 1,
          warningCount: 1,
          missingRequiredDestinationFields: ['body.org_id'],
          unmappedSourceFields: ['id'],
          unmappedDestinationFields: ['body.org_id'],
          schemaDriftDetected: true,
          sourceDriftDetected: false,
          destinationDriftDetected: true,
          issues: [
            {
              severity: 'warning',
              code: 'DESTINATION_SCHEMA_DRIFT',
              message: 'Destination snapshot changed.',
            },
            {
              severity: 'error',
              code: 'MISSING_REQUIRED_FIELD',
              message: 'Required field body.org_id is unmapped.',
              destinationFieldPath: 'body.org_id',
            },
          ],
          validatedAt: '2026-11-27T17:10:00+00:00',
        },
      })
    }
    return jsonResponse({ errorCode: 'NOT_FOUND', message: 'Unexpected' }, 404)
  }

  const listRes = await listMappings(
    {
      sourceConnectionId: SRC_CONN_ID,
      destinationConnectionId: DST_CONN_ID,
      status: 'draft',
      q: 'Customers',
      limit: 20,
      offset: 0,
    },
    { fetcher },
  )
  assert.equal(listRes.total, 1)
  assert.equal(listRes.items[0]?.id, MAP_ID)

  const created = await createMapping(
    {
      name: 'Customers to CRM',
      sourceConnectionId: SRC_CONN_ID,
      sourceObjectKey: 'public.customers',
      destinationConnectionId: DST_CONN_ID,
      destinationObjectKey: 'POST /v1/contacts',
      rules: [],
    },
    { fetcher },
  )
  assert.equal(created.status, 'draft')

  const detail = await getMapping(MAP_ID, { fetcher })
  assert.equal(detail.rules.length, 2)

  const updated = await updateMapping(
    MAP_ID,
    { name: 'Updated Name' },
    { fetcher },
  )
  assert.equal(updated.mappingVersion, 2)

  const validation = await validateMapping(MAP_ID, { fetcher })
  assert.equal(validation.status, 'invalid')
  assert.equal(validation.validationSummary.destinationDriftDetected, true)

  await deleteMapping(MAP_ID, { fetcher })

  assert.equal(calls.length, 6)
  assert.ok(calls[0]?.url.includes('sourceConnectionId='))
  assert.ok(calls[0]?.url.includes('destinationConnectionId='))
  assert.ok(calls[0]?.url.includes('status=draft'))
  assert.ok(calls[0]?.url.includes('q=Customers'))
  assert.ok(calls[0]?.url.includes('limit=20'))
  assert.ok(calls[0]?.url.includes('offset=0'))
})

test('mappingService handles version conflict, API errors, abort, and network failure', async () => {
  await assert.rejects(
    () =>
      validateMapping(MAP_ID, {
        fetcher: async () =>
          jsonResponse(
            {
              errorCode: 'MAPPING_VERSION_CONFLICT',
              message: 'Mapping was modified concurrently during validation.',
            },
            409,
          ),
      }),
    (err: unknown) =>
      err instanceof MappingApiClientError &&
      err.status === 409 &&
      err.errorCode === 'MAPPING_VERSION_CONFLICT',
  )

  const abortController = new AbortController()
  abortController.abort()
  await assert.rejects(
    () =>
      listMappings(
        {},
        { signal: abortController.signal, fetcher: async () => jsonResponse({}) },
      ),
    (err: unknown) => err instanceof MappingApiClientError && err.isAborted,
  )

  await assert.rejects(
    () =>
      listMappings(
        {},
        {
          fetcher: async () => {
            throw new TypeError('Failed to fetch')
          },
        },
      ),
    (err: unknown) =>
      err instanceof MappingApiClientError && err.errorCode === 'NETWORK_ERROR',
  )
})

test('mappingHelpers validates constant JSON, structural transforms, duplicates, ordering, and partial updates', () => {
  assert.deepEqual(parseConstantJsonInput('null'), { ok: true, value: null })
  assert.deepEqual(parseConstantJsonInput('"prod"'), { ok: true, value: 'prod' })
  assert.deepEqual(parseConstantJsonInput('42'), { ok: true, value: 42 })
  assert.deepEqual(parseConstantJsonInput('true'), { ok: true, value: true })
  assert.deepEqual(parseConstantJsonInput('["a", 1]'), {
    ok: true,
    value: ['a', 1],
  })
  assert.deepEqual(parseConstantJsonInput('{"env":"prod"}'), {
    ok: true,
    value: { env: 'prod' },
  })
  assert.equal(parseConstantJsonInput('unquoted_text').ok, false)
  assert.equal(parseConstantJsonInput('{"apiKey":"leak"}').ok, false)

  const sample = makeSampleRecord()
  const drafts = sample.rules.map(ruleResponseToDraft)
  drafts.push({
    localKey: 'tr-1',
    mappingType: 'transform',
    sourceFieldPath: 'full_name',
    destinationFieldPath: 'body.name',
    constantJsonText: '"value"',
    transformPresetName: 'trim_collapse',
    transformId: '',
    transformParams: [{ key: 'locale', value: 'en_US', valueKind: 'string' }],
    ordinalPosition: 2,
    metadata: {},
  })

  const serialized = serializeRuleDrafts(drafts)
  assert.equal(serialized.ok, true)
  if (serialized.ok) {
    assert.equal('constantValue' in serialized.rules[0], false)
    assert.equal(serialized.rules[1]?.constantValue, null)
    assert.equal(
      serialized.rules[2]?.transformRef?.presetName,
      'trim_collapse',
    )
    assert.deepEqual(
      serialized.rules.map((r) => r.ordinalPosition),
      [0, 1, 2],
    )
  }

  const reordered = reorderRuleDrafts(drafts, 2, 0)
  assert.equal(reordered.length, 3)
  assert.deepEqual(
    reordered.map((r) => [r.destinationFieldPath, r.ordinalPosition]),
    [
      ['body.name', 0],
      ['body.email', 1],
      ['body.source_system', 2],
    ],
  )

  const dupRes = serializeRuleDrafts([
    ...drafts,
    { ...drafts[0], localKey: 'dup-key', ordinalPosition: 3 },
  ])
  assert.equal(dupRes.ok, false)

  const execRes = serializeRuleDrafts([
    {
      ...drafts[2],
      transformParams: [
        { key: 'expression', value: 'x.upper()', valueKind: 'string' },
      ],
    },
  ])
  assert.equal(execRes.ok, false)

  const unchangedForm: MappingEditorFormState = {
    name: 'Customers to CRM v2',
    description: sample.description ?? '',
    sourceConnectionId: sample.sourceConnectionId,
    sourceObjectKey: sample.sourceObjectKey,
    sourceSchemaFingerprint: sample.sourceSchemaFingerprint,
    destinationConnectionId: sample.destinationConnectionId,
    destinationObjectKey: sample.destinationObjectKey,
    destinationSchemaFingerprint: sample.destinationSchemaFingerprint,
    rules: sample.rules.map(ruleResponseToDraft),
  }
  const origRulesSerialized = serializeRuleDrafts(unchangedForm.rules)
  assert.equal(origRulesSerialized.ok, true)
  if (origRulesSerialized.ok) {
    const partial = buildPartialUpdatePayload(
      sample,
      unchangedForm,
      origRulesSerialized.rules,
    )
    assert.deepEqual(partial, { name: 'Customers to CRM v2' })
    assert.equal('rules' in partial, false)
    assert.equal('sourceConnectionId' in partial, false)

    // Syncing destination fingerprint on same object emits destinationSchemaFingerprint only.
    const driftSyncedForm: MappingEditorFormState = {
      ...unchangedForm,
      name: sample.name,
      destinationSchemaFingerprint: 'c'.repeat(64),
    }
    const syncPartial = buildPartialUpdatePayload(
      sample,
      driftSyncedForm,
      origRulesSerialized.rules,
    )
    assert.deepEqual(syncPartial, {
      destinationSchemaFingerprint: 'c'.repeat(64),
    })
  }
})

test('required destination helper respects defaults, generated flags, and nullable fields', () => {
  const baseField: SchemaField = {
    id: 'f1',
    schemaObjectId: 'o1',
    fieldPath: 'col',
    fieldName: 'col',
    nativeType: 'integer',
    canonicalType: 'integer',
    nullable: false,
    isRequired: true,
    primaryKey: false,
    uniqueKey: false,
    defaultMetadata: null,
    ordinalPosition: 0,
    discoverySource: 'catalog',
    metadata: {},
    createdAt: '2026-11-27T17:00:00+00:00',
    updatedAt: '2026-11-27T17:00:00+00:00',
  }

  assert.equal(isEffectivelyRequiredDestinationField(baseField), true)
  assert.equal(
    isEffectivelyRequiredDestinationField({ ...baseField, nullable: true }),
    false,
  )
  assert.equal(
    isEffectivelyRequiredDestinationField({
      ...baseField,
      defaultMetadata: { hasDefault: true, isGenerated: false },
    }),
    false,
  )
  assert.equal(
    isEffectivelyRequiredDestinationField({
      ...baseField,
      defaultMetadata: { hasDefault: false, isGenerated: true },
    }),
    false,
  )
  assert.equal(getMappingStatusDisplay('valid').label, 'Valid')
  assert.equal(getMappingStatusDisplay('invalid').label, 'Invalid')
  assert.equal(getMappingStatusDisplay('draft').label, 'Draft')
})

test('Mapping Studio modules never use browser storage, mock fallbacks, eval, or secrets', () => {
  const filesToAudit = [
    path.join(webSrcDir, 'types', 'mapping.ts'),
    path.join(webSrcDir, 'services', 'mappingService.ts'),
    path.join(webSrcDir, 'pages', 'MappingStudioPage.tsx'),
    path.join(webSrcDir, 'components', 'mapping-studio', 'mappingHelpers.ts'),
    path.join(webSrcDir, 'components', 'mapping-studio', 'MappingListView.tsx'),
    path.join(webSrcDir, 'components', 'mapping-studio', 'MappingEditorView.tsx'),
    path.join(
      webSrcDir,
      'components',
      'mapping-studio',
      'DeleteMappingDialog.tsx',
    ),
    path.join(
      webSrcDir,
      'components',
      'mapping-studio',
      'ValidationSummaryPanel.tsx',
    ),
  ]

  for (const filePath of filesToAudit) {
    const src = fs.readFileSync(filePath, 'utf8')
    assert.ok(!src.includes('localStorage'), `localStorage in ${filePath}`)
    assert.ok(!src.includes('sessionStorage'), `sessionStorage in ${filePath}`)
    assert.ok(!src.includes('indexedDB'), `indexedDB in ${filePath}`)
    assert.ok(!src.includes('MOCK_CONNECTIONS'), `MOCK_CONNECTIONS in ${filePath}`)
    assert.ok(!src.includes('mockDashboardData'), `mockDashboardData in ${filePath}`)
    assert.ok(!/\beval\s*\(/.test(src), `eval() in ${filePath}`)
    assert.ok(!src.includes('new Function'), `new Function in ${filePath}`)
  }

  const editorSrc = fs.readFileSync(
    path.join(webSrcDir, 'components', 'mapping-studio', 'MappingEditorView.tsx'),
    'utf8',
  )
  assert.ok(editorSrc.includes('sourceEpochRef'))
  assert.ok(editorSrc.includes('destEpochRef'))
  assert.ok(editorSrc.includes('AbortController'))
  assert.ok(editorSrc.includes('MAPPING_VERSION_CONFLICT'))
  assert.ok(editorSrc.includes('/data-explorer?connectionId='))
})
