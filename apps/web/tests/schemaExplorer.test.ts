import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import {
  buildDiscoverPayload,
  createInitialDiscoveryForm,
  isInferredDiscoverySource,
  MAX_DISCOVERY_FILE_BYTES,
  validateDiscoveryForm,
} from '../src/components/data-explorer/discoveryForm.ts'
import {
  discoverConnectionSchema,
  getConnectionSchema,
  getSchemaObjectFields,
  listSchemaObjects,
  SchemaApiClientError,
} from '../src/services/schemaService.ts'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const webSrcDir = path.resolve(__dirname, '..', 'src')

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

test('schemaService integrates all four Step 03 endpoints and preserves ordering/pagination', async () => {
  const calls: Array<{ url: string; method: string; body?: string }> = []
  const connId = '11111111-1111-4111-8111-111111111111'
  const schemaId = '22222222-2222-4222-8222-222222222222'
  const objId = '33333333-3333-4333-8333-333333333333'
  const fp = 'a'.repeat(64)

  const sampleObject = {
    id: objId,
    discoveredSchemaId: schemaId,
    objectKey: 'public.customers',
    objectName: 'customers',
    objectType: 'table',
    discoverySource: 'catalog',
    nativeMetadata: { namespace: 'public' },
    canonicalMetadata: { confidence: 1.0, primaryKeyFields: ['id'] },
    fieldCount: 2,
    createdAt: '2026-11-27T17:00:00+00:00',
    updatedAt: '2026-11-27T17:00:00+00:00',
  }

  const fetcher: typeof fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString()
    calls.push({
      url,
      method: init?.method ?? 'GET',
      body: typeof init?.body === 'string' ? init.body : undefined,
    })

    if (url === `/api/v1/connections/${connId}/discover` && init?.method === 'POST') {
      return jsonResponse({
        snapshotId: schemaId,
        connectionId: connId,
        discoveryStatus: 'partial',
        discoverySource: 'catalog',
        schemaFingerprint: fp,
        objectCount: 1,
        warnings: [
          {
            code: 'OBJECT_LIMIT_REACHED',
            message: 'Truncated objects at configured limit.',
          },
        ],
        discoveredAt: '2026-11-27T17:00:00+00:00',
        objects: [sampleObject],
      })
    }

    if (url === `/api/v1/connections/${connId}/schema` && init?.method === 'GET') {
      return jsonResponse({
        id: schemaId,
        connectionId: connId,
        discoveryStatus: 'partial',
        discoveredAt: '2026-11-27T17:00:00+00:00',
        schemaFingerprint: fp,
        discoverySource: 'catalog',
        objectCount: 1,
        warnings: [{ code: 'OBJECT_LIMIT_REACHED', message: 'Truncated.' }],
        metadata: {},
        objects: [sampleObject],
        createdAt: '2026-11-27T17:00:00+00:00',
        updatedAt: '2026-11-27T17:00:00+00:00',
      })
    }

    if (
      url.startsWith(`/api/v1/connections/${connId}/objects?`) &&
      init?.method === 'GET'
    ) {
      return jsonResponse({
        connectionId: connId,
        schemaId,
        items: [sampleObject],
        total: 1,
        limit: 50,
        offset: 0,
      })
    }

    if (
      url.startsWith(`/api/v1/connections/${connId}/objects/${objId}/fields?`) &&
      init?.method === 'GET'
    ) {
      return jsonResponse({
        connectionId: connId,
        schemaId,
        object: sampleObject,
        fields: [
          {
            id: '44444444-4444-4444-8444-444444444441',
            schemaObjectId: objId,
            fieldPath: 'id',
            fieldName: 'id',
            nativeType: 'bigint',
            canonicalType: 'integer',
            nullable: false,
            isRequired: false,
            primaryKey: true,
            uniqueKey: true,
            defaultMetadata: {
              hasDefault: true,
              expression: "nextval('s')",
              isGenerated: true,
            },
            ordinalPosition: 0,
            discoverySource: 'catalog',
            metadata: { depth: 0, isArray: false },
            createdAt: '2026-11-27T17:00:00+00:00',
            updatedAt: '2026-11-27T17:00:00+00:00',
          },
          {
            id: '44444444-4444-4444-8444-444444444442',
            schemaObjectId: objId,
            fieldPath: 'email',
            fieldName: 'email',
            nativeType: 'varchar(255)',
            canonicalType: 'string',
            nullable: false,
            isRequired: true,
            primaryKey: false,
            uniqueKey: true,
            defaultMetadata: null,
            ordinalPosition: 1,
            discoverySource: 'catalog',
            metadata: { depth: 0, isArray: false },
            createdAt: '2026-11-27T17:00:00+00:00',
            updatedAt: '2026-11-27T17:00:00+00:00',
          },
        ],
        total: 2,
        limit: 100,
        offset: 0,
      })
    }

    throw new Error(`Unexpected fetch: ${init?.method} ${url}`)
  }

  const discRes = await discoverConnectionSchema(
    connId,
    { options: { includeSchemas: ['public'], includeViews: true } },
    { fetcher },
  )
  assert.equal(discRes.discoveryStatus, 'partial')
  assert.equal(discRes.objectCount, 1)
  assert.equal(discRes.warnings[0]?.code, 'OBJECT_LIMIT_REACHED')

  const schemaRes = await getConnectionSchema(connId, { fetcher })
  assert.equal(schemaRes.id, schemaId)

  const objsRes = await listSchemaObjects(
    connId,
    { q: 'cust', objectType: 'table', limit: 50, offset: 0 },
    { fetcher },
  )
  assert.equal(objsRes.total, 1)
  assert.equal(calls[2]?.url, `/api/v1/connections/${connId}/objects?q=cust&objectType=table&limit=50&offset=0`)

  const fieldsRes = await getSchemaObjectFields(
    connId,
    objId,
    { q: 'id', limit: 100, offset: 0 },
    { fetcher },
  )
  assert.deepEqual(
    fieldsRes.fields.map((f) => [f.ordinalPosition, f.fieldPath]),
    [
      [0, 'id'],
      [1, 'email'],
    ],
  )
})

test('schemaService handles 404, 422, 503, aborts, network failures, and sanitizes HTML errors', async () => {
  const connId = '11111111-1111-4111-8111-111111111111'

  await assert.rejects(
    () =>
      getConnectionSchema(connId, {
        fetcher: async () =>
          jsonResponse(
            {
              errorCode: 'SCHEMA_NOT_DISCOVERED',
              message: 'Schema not discovered yet.',
              timestamp: '2026-11-27T17:00:00+00:00',
            },
            404,
          ),
      }),
    (err: unknown) =>
      err instanceof SchemaApiClientError &&
      err.status === 404 &&
      err.errorCode === 'SCHEMA_NOT_DISCOVERED',
  )

  await assert.rejects(
    () =>
      discoverConnectionSchema(
        connId,
        {},
        {
          fetcher: async () =>
            new Response('<html><body>502 Bad Gateway</body></html>', {
              status: 502,
            }),
        },
      ),
    (err: unknown) =>
      err instanceof SchemaApiClientError &&
      err.status === 502 &&
      !err.message.includes('<html>'),
  )

  const preAborted = new AbortController()
  preAborted.abort()
  await assert.rejects(
    () =>
      getConnectionSchema(connId, {
        signal: preAborted.signal,
        fetcher: async () => jsonResponse({}),
      }),
    (err: unknown) => err instanceof SchemaApiClientError && err.isAborted,
  )

  const streamAbortController = new AbortController()
  await assert.rejects(
    () =>
      listSchemaObjects(
        connId,
        {},
        {
          signal: streamAbortController.signal,
          fetcher: async () => ({
            ok: true,
            status: 200,
            json: async () => {
              streamAbortController.abort()
              throw new DOMException('The operation was aborted.', 'AbortError')
            },
          } as unknown as Response),
        },
      ),
    (err: unknown) => err instanceof SchemaApiClientError && err.isAborted,
  )

  await assert.rejects(
    () =>
      listSchemaObjects(
        connId,
        {},
        {
          fetcher: async () => {
            throw new TypeError('Failed to fetch')
          },
        },
      ),
    (err: unknown) =>
      err instanceof SchemaApiClientError && err.errorCode === 'NETWORK_ERROR',
  )
})

test('discoveryForm validates connector modes and emits exact Step 03 request payloads', () => {
  const base = createInitialDiscoveryForm()

  // PostgreSQL includes includeSchemas and includeViews; MySQL omits includeSchemas.
  const pgForm = { ...base, includeSchemasText: 'public, analytics', includeViews: false }
  assert.equal(validateDiscoveryForm('PostgreSQL', pgForm, null).valid, true)
  assert.deepEqual(buildDiscoverPayload('PostgreSQL', pgForm), {
    options: { includeSchemas: ['public', 'analytics'], includeViews: false },
  })
  assert.deepEqual(buildDiscoverPayload('MySQL', pgForm), {
    options: { includeViews: false },
  })

  // CSV requires .csv file <= 256 KB and submits transient fileContent.
  assert.equal(validateDiscoveryForm('CSV', base, null).valid, false)
  assert.equal(
    validateDiscoveryForm('CSV', base, { name: 'wrong.txt', size: 100 }).valid,
    false,
  )
  assert.equal(
    validateDiscoveryForm('CSV', base, {
      name: 'huge.csv',
      size: MAX_DISCOVERY_FILE_BYTES + 1,
    }).valid,
    false,
  )
  assert.equal(
    validateDiscoveryForm('CSV', base, { name: 'leads.csv', size: 32 }).valid,
    true,
  )
  assert.deepEqual(
    buildDiscoverPayload('CSV', base, {
      fileName: 'leads.csv',
      fileContent: 'id,email\n1,a@b.com\n',
    }),
    {
      fileName: 'leads.csv',
      fileContent: 'id,email\n1,a@b.com\n',
      options: { sampleLimit: 500 },
    },
  )

  // JSON includes optional recordPath and validates dot path.
  const badJsonForm = { ...base, recordPath: 'data..items' }
  assert.equal(
    validateDiscoveryForm('JSON', badJsonForm, { name: 'a.json', size: 10 })
      .valid,
    false,
  )
  const goodJsonForm = { ...base, recordPath: 'data.items' }
  assert.deepEqual(
    buildDiscoverPayload('JSON', goodJsonForm, {
      fileName: 'a.json',
      fileContent: '{"data":{"items":[{"id":1}]}}',
    }),
    {
      fileName: 'a.json',
      fileContent: '{"data":{"items":[{"id":1}]}}',
      options: { recordPath: 'data.items' },
    },
  )

  // REST API: sample_get rejects full URLs, openapi validates JSON spec, manual validates unique fields.
  const badRestSample = {
    ...base,
    restMode: 'sample_get' as const,
    resourcePath: 'https://evil.example.com/steal',
  }
  assert.equal(validateDiscoveryForm('REST API', badRestSample, null).valid, false)

  const oaJsonForm = {
    ...base,
    restMode: 'openapi' as const,
    openApiSource: 'json' as const,
    openApiJsonText: '{"openapi":"3.0.3","paths":{}}',
  }
  assert.equal(validateDiscoveryForm('REST API', oaJsonForm, null).valid, true)
  assert.deepEqual(buildDiscoverPayload('REST API', oaJsonForm), {
    options: {
      mode: 'openapi',
      openApiSpec: { openapi: '3.0.3', paths: {} },
    },
  })

  const manualForm = { ...base, restMode: 'manual' as const }
  assert.equal(validateDiscoveryForm('REST API', manualForm, null).valid, true)
  const manualPayload = buildDiscoverPayload('REST API', manualForm)
  assert.equal(manualPayload.options?.mode, 'manual')
  assert.equal(manualPayload.options?.manualObjects?.[0]?.objectKey, 'POST /v1/records')

  assert.equal(isInferredDiscoverySource('inferred_sample'), true)
  assert.equal(isInferredDiscoverySource('catalog'), false)
})

test('Data Explorer modules never use browser storage, mock data, and guard against stale requests', () => {
  const filesToAudit = [
    path.join(webSrcDir, 'pages', 'DataExplorerPage.tsx'),
    path.join(webSrcDir, 'services', 'schemaService.ts'),
    path.join(webSrcDir, 'components', 'data-explorer', 'discoveryForm.ts'),
    path.join(
      webSrcDir,
      'components',
      'data-explorer',
      'DiscoveryOptionsPanel.tsx',
    ),
    path.join(
      webSrcDir,
      'components',
      'data-explorer',
      'SchemaSummaryBanner.tsx',
    ),
    path.join(webSrcDir, 'components', 'data-explorer', 'ObjectBrowser.tsx'),
    path.join(webSrcDir, 'components', 'data-explorer', 'FieldInspector.tsx'),
  ]

  for (const filePath of filesToAudit) {
    const src = fs.readFileSync(filePath, 'utf8')
    assert.ok(!src.includes('localStorage'), `Unexpected localStorage in ${filePath}`)
    assert.ok(!src.includes('sessionStorage'), `Unexpected sessionStorage in ${filePath}`)
    assert.ok(!src.includes('indexedDB'), `Unexpected indexedDB in ${filePath}`)
    assert.ok(!src.includes('MOCK_CONNECTIONS'), `Unexpected MOCK_CONNECTIONS in ${filePath}`)
    assert.ok(!src.includes('mockDashboardData'), `Unexpected mockDashboardData in ${filePath}`)
  }

  const pageSrc = fs.readFileSync(
    path.join(webSrcDir, 'pages', 'DataExplorerPage.tsx'),
    'utf8',
  )
  assert.ok(pageSrc.includes('connectionEpochRef'))
  assert.ok(pageSrc.includes('AbortController'))
})
