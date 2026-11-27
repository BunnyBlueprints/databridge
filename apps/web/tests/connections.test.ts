import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import {
  buildCreateConnectionPayload,
  buildUpdateConnectionPayload,
  createFormValuesFromConnection,
  createInitialFormValues,
  stripSensitiveFormValues,
  validateConnectorForm,
} from '../src/components/connections/connectorForm.ts'
import {
  ConnectionApiClientError,
  createConnection,
  deleteConnection,
  getConnection,
  listConnections,
  updateConnection,
} from '../src/services/connectionService.ts'
import {
  testConnection,
  testSavedConnection,
} from '../src/services/connectionTestService.ts'
import type { Connection } from '../src/types/connection.ts'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const webSrcDir = path.resolve(__dirname, '..', 'src')

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

test('connectionService constructs REST requests, query params, and parses safe responses', async () => {
  const calls: Array<{ url: string; method: string; body?: string }> = []
  const sampleConn: Connection = {
    id: '11111111-1111-4111-8111-111111111111',
    name: 'orders-pg',
    type: 'PostgreSQL',
    status: 'Disconnected',
    endpoint: 'pg.internal:5432/orders',
    config: { host: 'pg.internal', port: 5432, database: 'orders' },
    details: {
      host: 'pg.internal',
      port: 5432,
      database: 'orders',
      authConfigured: true,
    },
  }

  const fetcher: typeof fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString()
    calls.push({
      url,
      method: init?.method ?? 'GET',
      body: typeof init?.body === 'string' ? init.body : undefined,
    })

    if (url.startsWith('/api/v1/connections?') && init?.method === 'GET') {
      return jsonResponse({ items: [sampleConn], total: 1, limit: 50, offset: 0 })
    }
    if (url === '/api/v1/connections' && init?.method === 'POST') {
      return jsonResponse(sampleConn, 201)
    }
    if (
      url === `/api/v1/connections/${sampleConn.id}` &&
      init?.method === 'GET'
    ) {
      return jsonResponse(sampleConn, 200)
    }
    if (
      url === `/api/v1/connections/${sampleConn.id}` &&
      init?.method === 'PUT'
    ) {
      return jsonResponse({ ...sampleConn, name: 'orders-pg-updated' }, 200)
    }
    if (
      url === `/api/v1/connections/${sampleConn.id}` &&
      init?.method === 'DELETE'
    ) {
      return new Response(null, { status: 204 })
    }
    throw new Error(`Unexpected request: ${init?.method} ${url}`)
  }

  const listed = await listConnections(
    { status: 'Disconnected', q: 'orders', limit: 50 },
    { fetcher },
  )
  assert.equal(listed.total, 1)
  assert.equal(listed.items[0]?.name, 'orders-pg')

  const created = await createConnection(
    {
      name: 'orders-pg',
      connectorType: 'PostgreSQL',
      config: { host: 'pg.internal', port: 5432, database: 'orders' },
      credentials: { username: 'ro_user', password: 'secret_password' },
    },
    { fetcher },
  )
  assert.equal(created.id, sampleConn.id)

  const loaded = await getConnection(sampleConn.id, { fetcher })
  assert.equal(loaded.endpoint, 'pg.internal:5432/orders')

  const updated = await updateConnection(
    sampleConn.id,
    { name: 'orders-pg-updated', credentialAction: 'keep' },
    { fetcher },
  )
  assert.equal(updated.name, 'orders-pg-updated')

  await deleteConnection(sampleConn.id, { fetcher })

  assert.equal(
    calls[0]?.url,
    '/api/v1/connections?status=Disconnected&q=orders&limit=50',
  )
  assert.equal(calls[1]?.method, 'POST')
  assert.equal(calls[4]?.method, 'DELETE')
})

test('connectionService sanitizes HTTP errors, redacts secrets, and never falls back to mock data', async () => {
  const fetcher: typeof fetch = async () =>
    jsonResponse(
      {
        errorCode: 'DUPLICATE_CONNECTION',
        message: 'Duplicate failed with Bearer super_secret_tok_99 for sql_secret_pw',
      },
      409,
    )

  await assert.rejects(
    () =>
      createConnection(
        {
          name: 'dup',
          connectorType: 'REST API',
          config: { baseUrl: 'https://api.example.com', authType: 'bearer' },
          credentials: { bearerToken: 'super_secret_tok_99' },
        },
        { fetcher },
      ),
    (err: unknown) => {
      assert.ok(err instanceof ConnectionApiClientError)
      assert.equal(err.status, 409)
      assert.equal(err.errorCode, 'DUPLICATE_CONNECTION')
      assert.ok(!err.message.includes('super_secret_tok_99'))
      return true
    },
  )

  const htmlErrorFetcher: typeof fetch = async () =>
    new Response('<html><body>Internal Traceback</body></html>', { status: 500 })

  await assert.rejects(
    () => listConnections({}, { fetcher: htmlErrorFetcher }),
    (err: unknown) => {
      assert.ok(err instanceof ConnectionApiClientError)
      assert.equal(err.status, 500)
      assert.equal(err.errorCode, 'SERVER_ERROR')
      assert.ok(!err.message.includes('<html'))
      return true
    },
  )
})

test('connectorForm separates credentials cleanly, never prefills secrets, and computes update actions', () => {
  const createValues = {
    ...createInitialFormValues('PostgreSQL'),
    name: 'prod-pg',
    host: 'pg.example.com',
    port: '5432',
    database: 'warehouse',
    username: 'secret_sql_user',
    password: 'secret_sql_password',
  }
  const createErrors = validateConnectorForm(createValues, { mode: 'create' })
  assert.deepEqual(createErrors, {})

  const createPayload = buildCreateConnectionPayload(createValues)
  assert.equal(createPayload.connectorType, 'PostgreSQL')
  assert.deepEqual(createPayload.config, {
    host: 'pg.example.com',
    port: 5432,
    database: 'warehouse',
  })
  assert.ok(!('username' in createPayload.config))
  assert.deepEqual(createPayload.credentials, {
    username: 'secret_sql_user',
    password: 'secret_sql_password',
  })

  // Edit mode never prefills username, password, or tokens
  const existingPg: Connection = {
    id: '22222222-2222-4222-8222-222222222222',
    name: 'prod-pg',
    type: 'PostgreSQL',
    status: 'Connected',
    endpoint: 'pg.example.com:5432/warehouse',
    config: { host: 'pg.example.com', port: 5432, database: 'warehouse' },
    details: {
      host: 'pg.example.com',
      port: 5432,
      database: 'warehouse',
      authConfigured: true,
    },
  }
  const editValues = createFormValuesFromConnection(existingPg)
  assert.equal(editValues.username, '')
  assert.equal(editValues.password, '')

  // Metadata-only edit uses credentialAction: 'keep' without credentials
  const metaUpdate = buildUpdateConnectionPayload(
    { ...editValues, name: 'prod-pg-renamed', description: 'Renamed' },
    existingPg,
  )
  assert.equal(metaUpdate.credentialAction, 'keep')
  assert.equal(metaUpdate.credentials, undefined)

  // SQL password without username rejected in edit mode
  const badSqlReplace = validateConnectorForm(
    { ...editValues, password: 'new_pw_only' },
    { mode: 'edit', existingConnection: existingPg },
  )
  assert.ok(badSqlReplace.username)

  // Valid SQL replace sends encrypted username + password
  const sqlReplace = buildUpdateConnectionPayload(
    { ...editValues, username: 'new_user', password: 'new_pw_only' },
    existingPg,
  )
  assert.equal(sqlReplace.credentialAction, 'replace')
  assert.deepEqual(sqlReplace.credentials, {
    username: 'new_user',
    password: 'new_pw_only',
  })

  // MongoDB clear action & no duplicate URI
  const existingMongo: Connection = {
    id: '33333333-3333-4333-8333-333333333333',
    name: 'mongo-conn',
    type: 'MongoDB',
    status: 'Connected',
    endpoint: 'mongodb+srv://cluster.example.net/events',
    config: { uri: 'mongodb+srv://cluster.example.net', database: 'events' },
    details: {
      sanitizedUri: 'mongodb+srv://cluster.example.net/events',
      database: 'events',
      authConfigured: true,
    },
  }
  const mongoEdit = createFormValuesFromConnection(existingMongo)
  assert.equal(mongoEdit.mongoUsername, '')
  assert.equal(mongoEdit.mongoPassword, '')
  const mongoClear = buildUpdateConnectionPayload(
    { ...mongoEdit, clearCredentials: true },
    existingMongo,
  )
  assert.equal(mongoClear.credentialAction, 'clear')
  assert.equal(mongoClear.credentials, undefined)

  // REST API auth transitions: none clears credentials; switching mode requires new token
  const existingRest: Connection = {
    id: '44444444-4444-4444-8444-444444444444',
    name: 'rest-conn',
    type: 'REST API',
    status: 'Connected',
    endpoint: 'https://api.example.com',
    config: { baseUrl: 'https://api.example.com', authType: 'api_key' },
    details: {
      baseUrl: 'https://api.example.com',
      authType: 'api_key',
      authConfigured: true,
    },
  }
  const restEdit = createFormValuesFromConnection(existingRest)
  assert.equal(restEdit.apiKey, '')
  assert.equal(restEdit.bearerToken, '')
  const restSwitchErrs = validateConnectorForm(
    { ...restEdit, authType: 'bearer', bearerToken: '' },
    { mode: 'edit', existingConnection: existingRest },
  )
  assert.ok(restSwitchErrs.bearerToken)

  const restClear = buildUpdateConnectionPayload(
    { ...restEdit, authType: 'none' },
    existingRest,
  )
  assert.equal(restClear.credentialAction, 'clear')

  // CSV path checks + stripSensitiveFormValues
  const badCsvErrs = validateConnectorForm(
    { ...createInitialFormValues('CSV'), name: 'csv', fileName: '../evil.csv' },
    { mode: 'create' },
  )
  assert.ok(badCsvErrs.fileName)

  const cleared = stripSensitiveFormValues(createValues)
  assert.equal(cleared.username, '')
  assert.equal(cleared.password, '')
})

test('testSavedConnection sends no credentials and preserves Task 03 stateless endpoint', async () => {
  const requests: Array<{ url: string; body: string }> = []
  const fetcher: typeof fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString()
    requests.push({ url, body: String(init?.body ?? '') })
    return jsonResponse({
      success: true,
      status: 'connected',
      message: 'Verified saved connection.',
      durationMs: 12,
      testedAt: '2026-11-27T11:25:00+00:00',
    })
  }

  const savedResult = await testSavedConnection(
    '55555555-5555-4555-8555-555555555555',
    undefined,
    { fetcher },
  )
  assert.equal(savedResult.success, true)
  assert.equal(
    requests[0]?.url,
    '/api/v1/connections/55555555-5555-4555-8555-555555555555/test',
  )
  assert.equal(requests[0]?.body, '{}')

  const csvSavedResult = await testSavedConnection(
    '66666666-6666-4666-8666-666666666666',
    { fileContent: 'id,name\n1,Alice\n' },
    { fetcher },
  )
  assert.equal(csvSavedResult.success, true)
  assert.equal(
    requests[1]?.body,
    JSON.stringify({ fileContent: 'id,name\n1,Alice\n' }),
  )

  const statelessResult = await testConnection(
    {
      connectorType: 'JSON',
      config: { fileName: 'check.json' },
      fileContent: '{"ok":true}',
    },
    { fetcher },
  )
  assert.equal(statelessResult.success, true)
  assert.equal(requests[2]?.url, '/api/v1/connections/test')
})

test('frontend connection modules never use browser storage or production mock state', () => {
  const filesToAudit = [
    path.join(webSrcDir, 'pages', 'ConnectionsPage.tsx'),
    path.join(webSrcDir, 'services', 'connectionService.ts'),
    path.join(webSrcDir, 'services', 'connectionTestService.ts'),
    path.join(webSrcDir, 'services', 'apiClientUtils.ts'),
    path.join(webSrcDir, 'components', 'connections', 'connectorForm.ts'),
    path.join(webSrcDir, 'components', 'connections', 'AddConnectionModal.tsx'),
    path.join(webSrcDir, 'components', 'connections', 'TestConnectionModal.tsx'),
    path.join(webSrcDir, 'components', 'connections', 'ConnectionDetailsModal.tsx'),
    path.join(webSrcDir, 'components', 'connections', 'DeleteConnectionDialog.tsx'),
  ]

  for (const filePath of filesToAudit) {
    const src = fs.readFileSync(filePath, 'utf8')
    assert.ok(!src.includes('localStorage'), `Unexpected localStorage in ${filePath}`)
    assert.ok(!src.includes('sessionStorage'), `Unexpected sessionStorage in ${filePath}`)
    assert.ok(!src.includes('indexedDB'), `Unexpected indexedDB in ${filePath}`)
    assert.ok(!src.includes('MOCK_CONNECTIONS'), `Unexpected MOCK_CONNECTIONS in ${filePath}`)
  }
})
