import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  Cable,
  Compass,
  Loader2,
  Radar,
  RefreshCw,
} from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  createInitialDiscoveryForm,
  isTransientFileConnector,
  MAX_DISCOVERY_FILE_BYTES,
  type DiscoveryFormValidationResult,
  type DiscoveryFormValues,
  validateDiscoveryForm,
  buildDiscoverPayload,
} from '../components/data-explorer/discoveryForm.ts'
import { DiscoveryOptionsPanel } from '../components/data-explorer/DiscoveryOptionsPanel.tsx'
import { FieldInspector } from '../components/data-explorer/FieldInspector.tsx'
import { ObjectBrowser } from '../components/data-explorer/ObjectBrowser.tsx'
import { SchemaSummaryBanner } from '../components/data-explorer/SchemaSummaryBanner.tsx'
import { Badge } from '../components/ui/Badge.tsx'
import { Button } from '../components/ui/Button.tsx'
import { Card, CardContent } from '../components/ui/Card.tsx'
import { ConnectorIcon } from '../components/ui/ConnectorIcon.tsx'
import {
  ConnectionApiClientError,
  listConnections,
} from '../services/connectionService.ts'
import {
  discoverConnectionSchema,
  getConnectionSchema,
  getSchemaObjectFields,
  listSchemaObjects,
  SchemaApiClientError,
} from '../services/schemaService.ts'
import type { Connection } from '../types/connection.ts'
import type {
  DiscoveredSchemaResponse,
  SchemaField,
  SchemaObjectSummary,
  SchemaObjectType,
} from '../types/schema.ts'

const OBJECTS_PAGE_LIMIT = 50
const FIELDS_PAGE_LIMIT = 100

interface SnapshotDisplayState {
  discoveryStatus: DiscoveredSchemaResponse['discoveryStatus']
  discoverySource: DiscoveredSchemaResponse['discoverySource']
  discoveredAt: string
  schemaFingerprint: string
  objectCount: number
  warnings: DiscoveredSchemaResponse['warnings']
}

export function DataExplorerPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialParamId = searchParams.get('connectionId')

  const [connections, setConnections] = useState<Connection[]>([])
  const [isLoadingConnections, setIsLoadingConnections] = useState(true)
  const [connectionsError, setConnectionsError] = useState<string | null>(null)
  const [selectedConnectionId, setSelectedConnectionId] = useState<string>(
    initialParamId ?? '',
  )

  const [discoveryForm, setDiscoveryForm] = useState<DiscoveryFormValues>(() =>
    createInitialDiscoveryForm(),
  )
  const [formErrors, setFormErrors] = useState<
    DiscoveryFormValidationResult['errors']
  >({})
  const [selectedFile, setSelectedFile] = useState<File | null>(null)

  const [isLoadingSchema, setIsLoadingSchema] = useState(false)
  const [schemaSummary, setSchemaSummary] =
    useState<SnapshotDisplayState | null>(null)
  const [schemaNotDiscovered, setSchemaNotDiscovered] = useState(false)
  const [schemaError, setSchemaError] = useState<string | null>(null)

  const [isDiscovering, setIsDiscovering] = useState(false)
  const [discoverError, setDiscoverError] = useState<string | null>(null)

  const [objects, setObjects] = useState<SchemaObjectSummary[]>([])
  const [objectsTotal, setObjectsTotal] = useState(0)
  const [objectSearch, setObjectSearch] = useState('')
  const [debouncedObjectSearch, setDebouncedObjectSearch] = useState('')
  const [objectTypeFilter, setObjectTypeFilter] = useState<
    SchemaObjectType | 'All'
  >('All')
  const [objectOffset, setObjectOffset] = useState(0)
  const [isLoadingObjects, setIsLoadingObjects] = useState(false)
  const [objectsError, setObjectsError] = useState<string | null>(null)
  const [selectedObject, setSelectedObject] =
    useState<SchemaObjectSummary | null>(null)

  const [fields, setFields] = useState<SchemaField[]>([])
  const [fieldsTotal, setFieldsTotal] = useState(0)
  const [fieldSearch, setFieldSearch] = useState('')
  const [debouncedFieldSearch, setDebouncedFieldSearch] = useState('')
  const [fieldOffset, setFieldOffset] = useState(0)
  const [isLoadingFields, setIsLoadingFields] = useState(false)
  const [fieldsError, setFieldsError] = useState<string | null>(null)

  const connectionsAbortRef = useRef<AbortController | null>(null)
  const schemaAbortRef = useRef<AbortController | null>(null)
  const objectsAbortRef = useRef<AbortController | null>(null)
  const fieldsAbortRef = useRef<AbortController | null>(null)
  const discoverAbortRef = useRef<AbortController | null>(null)
  const connectionEpochRef = useRef(0)
  const preserveObjectKeyRef = useRef<string | null>(null)

  const selectedConnection = useMemo(
    () => connections.find((c) => c.id === selectedConnectionId) ?? null,
    [connections, selectedConnectionId],
  )

  useEffect(() => {
    const timer = globalThis.setTimeout(() => {
      setDebouncedObjectSearch(objectSearch.trim())
      setObjectOffset(0)
    }, 180)
    return () => globalThis.clearTimeout(timer)
  }, [objectSearch])

  useEffect(() => {
    const timer = globalThis.setTimeout(() => {
      setDebouncedFieldSearch(fieldSearch.trim())
      setFieldOffset(0)
    }, 180)
    return () => globalThis.clearTimeout(timer)
  }, [fieldSearch])

  const loadConnections = useCallback(async () => {
    connectionsAbortRef.current?.abort()
    const controller = new AbortController()
    connectionsAbortRef.current = controller

    setIsLoadingConnections(true)
    setConnectionsError(null)
    try {
      const res = await listConnections(
        { limit: 200 },
        { signal: controller.signal },
      )
      if (controller.signal.aborted) return
      setConnections(res.items)
      setSelectedConnectionId((prev) => {
        if (prev && res.items.some((c) => c.id === prev)) return prev
        return res.items[0]?.id ?? ''
      })
    } catch (err) {
      if (err instanceof ConnectionApiClientError && err.isAborted) return
      setConnectionsError(
        err instanceof Error
          ? err.message
          : 'Unable to load saved connections.',
      )
    } finally {
      if (!controller.signal.aborted) {
        setIsLoadingConnections(false)
      }
    }
  }, [])

  useEffect(() => {
    void loadConnections()
    return () => {
      connectionsAbortRef.current?.abort()
      schemaAbortRef.current?.abort()
      objectsAbortRef.current?.abort()
      fieldsAbortRef.current?.abort()
      discoverAbortRef.current?.abort()
    }
  }, [loadConnections])

  const loadObjects = useCallback(
    async (
      connectionId: string,
      epoch: number,
      opts?: {
        q?: string
        objectType?: SchemaObjectType | 'All'
        offset?: number
        preserveObjectKey?: string | null
      },
    ) => {
      objectsAbortRef.current?.abort()
      const controller = new AbortController()
      objectsAbortRef.current = controller

      setIsLoadingObjects(true)
      setObjectsError(null)
      try {
        const targetQ = opts?.q ?? debouncedObjectSearch
        const targetType = opts?.objectType ?? objectTypeFilter
        const targetOffset = opts?.offset ?? objectOffset

        const res = await listSchemaObjects(
          connectionId,
          {
            ...(targetQ ? { q: targetQ } : {}),
            ...(targetType !== 'All' ? { objectType: targetType } : {}),
            limit: OBJECTS_PAGE_LIMIT,
            offset: targetOffset,
          },
          { signal: controller.signal },
        )
        if (controller.signal.aborted || epoch !== connectionEpochRef.current) {
          return
        }

        setObjects(res.items)
        setObjectsTotal(res.total)
        const pendingPreserveKey =
          opts?.preserveObjectKey ?? preserveObjectKeyRef.current
        preserveObjectKeyRef.current = null
        setSelectedObject((prev) => {
          const desiredKey = pendingPreserveKey ?? prev?.objectKey ?? null
          if (desiredKey) {
            const matchByKey = res.items.find((o) => o.objectKey === desiredKey)
            if (matchByKey) return matchByKey
          }
          if (prev) {
            const matchById = res.items.find((o) => o.id === prev.id)
            if (matchById) return matchById
          }
          return res.items[0] ?? null
        })
      } catch (err) {
        if (err instanceof SchemaApiClientError && err.isAborted) return
        if (epoch !== connectionEpochRef.current) return
        setObjectsError(
          err instanceof Error
            ? err.message
            : 'Unable to load discovered objects.',
        )
      } finally {
        if (
          !controller.signal.aborted &&
          epoch === connectionEpochRef.current
        ) {
          setIsLoadingObjects(false)
        }
      }
    },
    [debouncedObjectSearch, objectTypeFilter, objectOffset],
  )

  const loadLatestSchema = useCallback(
    async (connectionId: string, epoch: number) => {
      schemaAbortRef.current?.abort()
      const controller = new AbortController()
      schemaAbortRef.current = controller

      setIsLoadingSchema(true)
      setSchemaError(null)
      setSchemaNotDiscovered(false)
      try {
        const snapshot = await getConnectionSchema(connectionId, {
          signal: controller.signal,
        })
        if (controller.signal.aborted || epoch !== connectionEpochRef.current) {
          return
        }
        setSchemaSummary({
          discoveryStatus: snapshot.discoveryStatus,
          discoverySource: snapshot.discoverySource,
          discoveredAt: snapshot.discoveredAt,
          schemaFingerprint: snapshot.schemaFingerprint,
          objectCount: snapshot.objectCount,
          warnings: snapshot.warnings,
        })
        setSchemaNotDiscovered(false)
      } catch (err) {
        if (err instanceof SchemaApiClientError && err.isAborted) return
        if (epoch !== connectionEpochRef.current) return
        if (
          err instanceof SchemaApiClientError &&
          (err.errorCode === 'SCHEMA_NOT_DISCOVERED' || err.status === 404)
        ) {
          setSchemaSummary(null)
          setSchemaNotDiscovered(true)
          setObjects([])
          setObjectsTotal(0)
          setSelectedObject(null)
          setFields([])
          setFieldsTotal(0)
        } else {
          setSchemaSummary(null)
          setSchemaError(
            err instanceof Error
              ? err.message
              : 'Failed to load discovered schema snapshot.',
          )
        }
      } finally {
        if (
          !controller.signal.aborted &&
          epoch === connectionEpochRef.current
        ) {
          setIsLoadingSchema(false)
        }
      }
    },
    [],
  )

  // Reset connection-scoped exploration state cleanly whenever selectedConnectionId changes.
  useEffect(() => {
    connectionEpochRef.current += 1
    const epoch = connectionEpochRef.current

    schemaAbortRef.current?.abort()
    objectsAbortRef.current?.abort()
    fieldsAbortRef.current?.abort()
    discoverAbortRef.current?.abort()

    setIsDiscovering(false)
    setDiscoverError(null)
    setFormErrors({})
    setSelectedFile(null)
    setDiscoveryForm(createInitialDiscoveryForm())

    setSchemaSummary(null)
    setSchemaNotDiscovered(false)
    setSchemaError(null)
    setObjects([])
    setObjectsTotal(0)
    setObjectSearch('')
    setDebouncedObjectSearch('')
    setObjectTypeFilter('All')
    setObjectOffset(0)
    setObjectsError(null)
    setSelectedObject(null)
    setFields([])
    setFieldsTotal(0)
    setFieldSearch('')
    setDebouncedFieldSearch('')
    setFieldOffset(0)
    setFieldsError(null)

    if (!selectedConnectionId) {
      return
    }

    setSearchParams(
      (prev) => {
        if (prev.get('connectionId') === selectedConnectionId) return prev
        const next = new URLSearchParams(prev)
        next.set('connectionId', selectedConnectionId)
        return next
      },
      { replace: true },
    )

    void loadLatestSchema(selectedConnectionId, epoch)
  }, [selectedConnectionId, loadLatestSchema, setSearchParams])

  // Fetch paginated/filtered objects when a schema snapshot is active.
  useEffect(() => {
    if (!selectedConnectionId || !schemaSummary || schemaNotDiscovered) {
      return
    }
    const epoch = connectionEpochRef.current
    void loadObjects(selectedConnectionId, epoch)
  }, [selectedConnectionId, schemaSummary, schemaNotDiscovered, loadObjects])

  const loadFields = useCallback(
    async (connectionId: string, objectId: string, epoch: number) => {
      fieldsAbortRef.current?.abort()
      const controller = new AbortController()
      fieldsAbortRef.current = controller

      setIsLoadingFields(true)
      setFieldsError(null)
      try {
        const res = await getSchemaObjectFields(
          connectionId,
          objectId,
          {
            ...(debouncedFieldSearch ? { q: debouncedFieldSearch } : {}),
            limit: FIELDS_PAGE_LIMIT,
            offset: fieldOffset,
          },
          { signal: controller.signal },
        )
        if (controller.signal.aborted || epoch !== connectionEpochRef.current) {
          return
        }
        setFields(res.fields)
        setFieldsTotal(res.total)
      } catch (err) {
        if (err instanceof SchemaApiClientError && err.isAborted) return
        if (epoch !== connectionEpochRef.current) return
        setFields([])
        setFieldsTotal(0)
        setFieldsError(
          err instanceof Error
            ? err.message
            : 'Unable to load fields for this object.',
        )
      } finally {
        if (
          !controller.signal.aborted &&
          epoch === connectionEpochRef.current
        ) {
          setIsLoadingFields(false)
        }
      }
    },
    [debouncedFieldSearch, fieldOffset],
  )

  useEffect(() => {
    if (!selectedConnectionId || !selectedObject) {
      setFields([])
      setFieldsTotal(0)
      return
    }
    const epoch = connectionEpochRef.current
    void loadFields(selectedConnectionId, selectedObject.id, epoch)
  }, [selectedConnectionId, selectedObject, loadFields])

  const handleSelectObject = (obj: SchemaObjectSummary) => {
    setSelectedObject(obj)
    setFieldSearch('')
    setDebouncedFieldSearch('')
    setFieldOffset(0)
  }

  const handleDiscover = async () => {
    if (!selectedConnection || isDiscovering) return

    const fileMeta = selectedFile
      ? { name: selectedFile.name, size: selectedFile.size }
      : null
    const validation = validateDiscoveryForm(
      selectedConnection.type,
      discoveryForm,
      fileMeta,
    )
    setFormErrors(validation.errors)
    if (!validation.valid) return

    const epoch = connectionEpochRef.current
    discoverAbortRef.current?.abort()
    const controller = new AbortController()
    discoverAbortRef.current = controller

    setIsDiscovering(true)
    setDiscoverError(null)

    try {
      let transientFile:
        | { fileName: string; fileContent: string }
        | undefined
      if (isTransientFileConnector(selectedConnection.type) && selectedFile) {
        const content = await selectedFile.text()
        if (controller.signal.aborted || epoch !== connectionEpochRef.current) {
          return
        }
        const byteLength = new TextEncoder().encode(content).length
        if (byteLength > MAX_DISCOVERY_FILE_BYTES) {
          setFormErrors({
            file: `File content exceeds maximum size of ${MAX_DISCOVERY_FILE_BYTES} bytes.`,
          })
          return
        }
        transientFile = {
          fileName: selectedFile.name,
          fileContent: content,
        }
      }

      const payload = buildDiscoverPayload(
        selectedConnection.type,
        discoveryForm,
        transientFile,
      )
      // Drop transient file content reference immediately once serialized by fetch.
      transientFile = undefined

      const prevObjectKey = selectedObject?.objectKey ?? null
      const result = await discoverConnectionSchema(
        selectedConnection.id,
        payload,
        { signal: controller.signal },
      )
      if (controller.signal.aborted || epoch !== connectionEpochRef.current) {
        return
      }

      setSchemaNotDiscovered(false)
      setSchemaError(null)
      preserveObjectKeyRef.current = prevObjectKey
      setObjectOffset(0)
      setSchemaSummary({
        discoveryStatus: result.discoveryStatus,
        discoverySource: result.discoverySource,
        discoveredAt: result.discoveredAt,
        schemaFingerprint: result.schemaFingerprint,
        objectCount: result.objectCount,
        warnings: result.warnings,
      })
    } catch (err) {
      if (err instanceof SchemaApiClientError && err.isAborted) return
      if (epoch !== connectionEpochRef.current) return
      setDiscoverError(
        err instanceof Error
          ? err.message
          : 'Schema discovery failed. Please verify the connection and try again.',
      )
    } finally {
      if (
        !controller.signal.aborted &&
        epoch === connectionEpochRef.current
      ) {
        setIsDiscovering(false)
      }
    }
  }

  return (
    <div className="space-y-5">
      {/* Page header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
              Data Explorer
            </h1>
            <Badge tone="zinc">{connections.length} connections</Badge>
          </div>
          <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
            Inspect persisted schema metadata, run read-only connector
            discovery, and review normalized object fields and types.
          </p>
        </div>

        <Button
          variant="secondary"
          size="sm"
          disabled={isLoadingConnections || isDiscovering}
          onClick={() => void loadConnections()}
          className="h-[34px] self-start sm:self-auto"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${isLoadingConnections ? 'animate-spin' : ''}`}
            aria-hidden="true"
          />
          <span>Refresh Connections</span>
        </Button>
      </div>

      {/* Connections Loading / Error / Empty / Selector */}
      {isLoadingConnections ? (
        <Card className="dark:border-zinc-800 dark:bg-zinc-900">
          <CardContent className="flex flex-col items-center justify-center px-6 py-12 text-center">
            <Loader2
              className="h-6 w-6 animate-spin text-emerald-600"
              aria-hidden="true"
            />
            <p className="mt-3 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              Loading saved connections…
            </p>
          </CardContent>
        </Card>
      ) : connectionsError ? (
        <Card className="dark:border-zinc-800 dark:bg-zinc-900">
          <CardContent className="px-6 py-12 text-center">
            <div role="alert" className="flex flex-col items-center">
              <span className="flex h-10 w-10 items-center justify-center rounded-full border border-rose-200 bg-rose-50 text-rose-600">
                <AlertCircle className="h-5 w-5" aria-hidden="true" />
              </span>
              <h2 className="mt-3 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Failed to load connections
              </h2>
              <p className="mt-1 max-w-md text-xs text-zinc-600 dark:text-zinc-400">
                {connectionsError}
              </p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void loadConnections()}
                className="mt-4"
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Retry</span>
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : connections.length === 0 ? (
        <Card className="dark:border-zinc-800 dark:bg-zinc-900">
          <CardContent className="flex flex-col items-center justify-center px-6 py-12 text-center">
            <span className="flex h-10 w-10 items-center justify-center rounded-full border border-zinc-200 bg-zinc-50 text-zinc-500">
              <Cable className="h-5 w-5" aria-hidden="true" />
            </span>
            <h2 className="mt-3 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              No saved connections available
            </h2>
            <p className="mt-1 max-w-sm text-xs text-zinc-500 dark:text-zinc-400">
              Create a PostgreSQL, MySQL, MongoDB, REST API, CSV, or JSON
              connection first before running schema discovery.
            </p>
            <Link
              to="/connections"
              className="mt-4 inline-flex h-8 items-center gap-1.5 rounded-md bg-emerald-600 px-3 text-xs font-medium text-white hover:bg-emerald-700 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none"
            >
              <span>Go to Connections</span>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Connection selector bar */}
          <Card className="dark:border-zinc-800 dark:bg-zinc-900">
            <CardContent className="flex flex-col gap-3 px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-1 flex-col gap-1.5 sm:max-w-md">
                <label
                  htmlFor="explorer-connection-select"
                  className="text-xs font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Target Connection
                </label>
                <select
                  id="explorer-connection-select"
                  value={selectedConnectionId}
                  onChange={(e) => setSelectedConnectionId(e.target.value)}
                  className="h-9 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs font-medium text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                >
                  {connections.map((conn) => (
                    <option key={conn.id} value={conn.id}>
                      {conn.name} ({conn.type})
                    </option>
                  ))}
                </select>
              </div>

              {selectedConnection && (
                <div className="flex flex-wrap items-center gap-2.5 text-xs">
                  <ConnectorIcon type={selectedConnection.type} size="sm" />
                  <span className="rounded border border-zinc-200 bg-zinc-50 px-2.5 py-1 font-mono text-[11px] text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-300">
                    {selectedConnection.endpoint}
                  </span>
                  <Badge
                    tone={
                      selectedConnection.status === 'Connected'
                        ? 'emerald'
                        : selectedConnection.status === 'Error'
                          ? 'rose'
                          : 'zinc'
                    }
                    dot
                  >
                    {selectedConnection.status}
                  </Badge>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Discovery Options & Trigger */}
          {selectedConnection && (
            <DiscoveryOptionsPanel
              connection={selectedConnection}
              values={discoveryForm}
              errors={formErrors}
              selectedFile={selectedFile}
              isDiscovering={isDiscovering}
              hasExistingSchema={Boolean(schemaSummary)}
              onChange={setDiscoveryForm}
              onSelectFile={(file) => {
                setSelectedFile(file)
                setFormErrors((prev) => ({ ...prev, file: undefined }))
              }}
              onDiscover={() => void handleDiscover()}
            />
          )}

          {/* Discovery execution error */}
          {discoverError && (
            <div
              role="alert"
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50/80 px-4 py-3 text-xs text-rose-900 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-200"
            >
              <div className="flex items-start gap-2">
                <AlertCircle
                  className="mt-0.5 h-4 w-4 shrink-0 text-rose-600 dark:text-rose-400"
                  aria-hidden="true"
                />
                <div>
                  <span className="font-semibold">Schema discovery failed: </span>
                  <span>{discoverError}</span>
                </div>
              </div>
              <Button
                variant="secondary"
                size="sm"
                disabled={isDiscovering || !selectedConnection}
                onClick={() => void handleDiscover()}
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Retry Discovery</span>
              </Button>
            </div>
          )}

          {/* Schema Snapshot Area */}
          {isLoadingSchema ? (
            <Card className="dark:border-zinc-800 dark:bg-zinc-900">
              <CardContent className="flex items-center justify-center px-6 py-12 text-xs text-zinc-500">
                <Loader2
                  className="mr-2 h-4 w-4 animate-spin text-emerald-600"
                  aria-hidden="true"
                />
                <span>Loading latest discovered schema…</span>
              </CardContent>
            </Card>
          ) : schemaError ? (
            <Card className="dark:border-zinc-800 dark:bg-zinc-900">
              <CardContent className="px-6 py-10 text-center">
                <div role="alert" className="flex flex-col items-center">
                  <AlertCircle
                    className="h-6 w-6 text-rose-600"
                    aria-hidden="true"
                  />
                  <h2 className="mt-2 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                    Unable to load schema snapshot
                  </h2>
                  <p className="mt-1 max-w-md text-xs text-zinc-600 dark:text-zinc-400">
                    {schemaError}
                  </p>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() =>
                      selectedConnectionId &&
                      void loadLatestSchema(
                        selectedConnectionId,
                        connectionEpochRef.current,
                      )
                    }
                    className="mt-3"
                  >
                    Retry
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : schemaNotDiscovered ? (
            <Card className="dark:border-zinc-800 dark:bg-zinc-900">
              <CardContent className="flex flex-col items-center justify-center px-6 py-12 text-center">
                <span className="flex h-10 w-10 items-center justify-center rounded-full border border-zinc-200 bg-zinc-50 text-zinc-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                  <Compass className="h-5 w-5" aria-hidden="true" />
                </span>
                <h2 className="mt-3 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                  Schema not discovered yet
                </h2>
                <p className="mt-1 max-w-md text-xs text-zinc-500 dark:text-zinc-400">
                  No schema snapshot is stored for{' '}
                  <strong className="font-medium text-zinc-800 dark:text-zinc-200">
                    {selectedConnection?.name}
                  </strong>
                  . Configure any connector options above and run discovery to
                  inspect objects and fields.
                </p>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={isDiscovering || !selectedConnection}
                  onClick={() => void handleDiscover()}
                  className="mt-4"
                >
                  <Radar className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Discover Schema</span>
                </Button>
              </CardContent>
            </Card>
          ) : schemaSummary ? (
            <div className="space-y-4">
              <SchemaSummaryBanner
                discoveryStatus={schemaSummary.discoveryStatus}
                discoverySource={schemaSummary.discoverySource}
                discoveredAt={schemaSummary.discoveredAt}
                schemaFingerprint={schemaSummary.schemaFingerprint}
                objectCount={schemaSummary.objectCount}
                warnings={schemaSummary.warnings}
              />

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
                <div className="lg:col-span-4">
                  <ObjectBrowser
                    objects={objects}
                    total={objectsTotal}
                    limit={OBJECTS_PAGE_LIMIT}
                    offset={objectOffset}
                    selectedObjectId={selectedObject?.id ?? null}
                    searchQuery={objectSearch}
                    objectTypeFilter={objectTypeFilter}
                    isLoading={isLoadingObjects}
                    error={objectsError}
                    onSearchChange={setObjectSearch}
                    onTypeFilterChange={(nextType) => {
                      setObjectTypeFilter(nextType)
                      setObjectOffset(0)
                    }}
                    onSelectObject={handleSelectObject}
                    onPageChange={setObjectOffset}
                    onRetry={() =>
                      selectedConnectionId &&
                      void loadObjects(
                        selectedConnectionId,
                        connectionEpochRef.current,
                      )
                    }
                  />
                </div>

                <div className="lg:col-span-8">
                  <FieldInspector
                    selectedObject={selectedObject}
                    fields={fields}
                    total={fieldsTotal}
                    limit={FIELDS_PAGE_LIMIT}
                    offset={fieldOffset}
                    fieldQuery={fieldSearch}
                    isLoading={isLoadingFields}
                    error={fieldsError}
                    onFieldQueryChange={setFieldSearch}
                    onPageChange={setFieldOffset}
                    onRetry={() =>
                      selectedConnectionId &&
                      selectedObject &&
                      void loadFields(
                        selectedConnectionId,
                        selectedObject.id,
                        connectionEpochRef.current,
                      )
                    }
                  />
                </div>
              </div>
            </div>
          ) : null}
        </>
      )}
    </div>
  )
}
