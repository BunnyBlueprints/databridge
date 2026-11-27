import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  Cable,
  CheckCircle2,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  X,
} from 'lucide-react'
import { AddConnectionModal } from '../components/connections/AddConnectionModal.tsx'
import { ConnectionCard } from '../components/connections/ConnectionCard.tsx'
import { ConnectionDetailsModal } from '../components/connections/ConnectionDetailsModal.tsx'
import { DeleteConnectionDialog } from '../components/connections/DeleteConnectionDialog.tsx'
import { TestConnectionModal } from '../components/connections/TestConnectionModal.tsx'
import { Button } from '../components/ui/Button.tsx'
import { Card, CardContent } from '../components/ui/Card.tsx'
import {
  ConnectionApiClientError,
  createConnection,
  deleteConnection,
  listConnections,
  updateConnection,
} from '../services/connectionService.ts'
import type {
  Connection,
  ConnectionStatus,
  CreateConnectionPayload,
  UpdateConnectionPayload,
} from '../types/connection.ts'

type StatusFilter = 'All' | ConnectionStatus

const STATUS_FILTERS: StatusFilter[] = [
  'All',
  'Connected',
  'Disconnected',
  'Error',
]

interface FeedbackBanner {
  title: string
  detail: string
}

export function ConnectionsPage() {
  const [connections, setConnections] = useState<Connection[]>([])
  const [totalFiltered, setTotalFiltered] = useState(0)
  const [allConnectionsSummary, setAllConnectionsSummary] = useState<
    Connection[]
  >([])
  const [totalConfigured, setTotalConfigured] = useState(0)

  const [isInitialLoading, setIsInitialLoading] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('All')

  const [isAddModalOpen, setIsAddModalOpen] = useState(false)
  const [editingConnection, setEditingConnection] =
    useState<Connection | null>(null)
  const [selectedConnection, setSelectedConnection] =
    useState<Connection | null>(null)
  const [testingConnection, setTestingConnection] =
    useState<Connection | null>(null)
  const [deletingConnection, setDeletingConnection] =
    useState<Connection | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [feedbackBanner, setFeedbackBanner] = useState<FeedbackBanner | null>(
    null,
  )

  const fetchAbortRef = useRef<AbortController | null>(null)
  const hasLoadedOnceRef = useRef(false)

  useEffect(() => {
    const timer = globalThis.setTimeout(() => {
      setDebouncedQuery(searchQuery.trim())
    }, 180)
    return () => globalThis.clearTimeout(timer)
  }, [searchQuery])

  const fetchConnectionsFromApi = useCallback(
    async (options?: {
      status?: StatusFilter
      q?: string
      syncSummary?: boolean
    }) => {
      fetchAbortRef.current?.abort()
      const controller = new AbortController()
      fetchAbortRef.current = controller

      const targetStatus = options?.status ?? statusFilter
      const targetQuery = options?.q ?? debouncedQuery
      const isFiltered = targetStatus !== 'All' || targetQuery.length > 0

      if (!hasLoadedOnceRef.current) {
        setIsInitialLoading(true)
      } else {
        setIsRefreshing(true)
      }
      setLoadError(null)

      try {
        const filteredPromise = listConnections(
          {
            ...(targetStatus !== 'All' ? { status: targetStatus } : {}),
            ...(targetQuery ? { q: targetQuery } : {}),
            limit: 200,
          },
          { signal: controller.signal },
        )

        const shouldFetchSummary =
          options?.syncSummary || (!isFiltered && options?.syncSummary !== false)
        const summaryPromise = isFiltered && options?.syncSummary
          ? listConnections({ limit: 200 }, { signal: controller.signal })
          : null

        const [filteredRes, summaryRes] = await Promise.all([
          filteredPromise,
          summaryPromise,
        ])

        if (controller.signal.aborted) return

        setConnections(filteredRes.items)
        setTotalFiltered(filteredRes.total)

        if (!isFiltered && shouldFetchSummary) {
          setAllConnectionsSummary(filteredRes.items)
          setTotalConfigured(filteredRes.total)
        } else if (summaryRes) {
          setAllConnectionsSummary(summaryRes.items)
          setTotalConfigured(summaryRes.total)
        }

        const latestItems = summaryRes?.items ?? filteredRes.items
        setSelectedConnection((prev) =>
          prev
            ? (latestItems.find((item) => item.id === prev.id) ?? null)
            : null,
        )
        setTestingConnection((prev) =>
          prev
            ? (latestItems.find((item) => item.id === prev.id) ?? null)
            : null,
        )

        hasLoadedOnceRef.current = true
      } catch (error) {
        if (
          error instanceof ConnectionApiClientError &&
          error.isAborted
        ) {
          return
        }
        if (error instanceof ConnectionApiClientError) {
          setLoadError(error.message)
        } else {
          setLoadError(
            'Unable to load connections from the backend API. Retry or verify server connectivity.',
          )
        }
      } finally {
        if (fetchAbortRef.current === controller) {
          setIsInitialLoading(false)
          setIsRefreshing(false)
        }
      }
    },
    [debouncedQuery, statusFilter],
  )

  useEffect(() => {
    void fetchConnectionsFromApi({
      status: statusFilter,
      q: debouncedQuery,
      syncSummary: statusFilter === 'All' && debouncedQuery === '',
    })
    return () => {
      fetchAbortRef.current?.abort()
    }
  }, [debouncedQuery, fetchConnectionsFromApi, statusFilter])

  const statusCounts = useMemo(() => {
    const counts: Record<StatusFilter, number> = {
      All: totalConfigured,
      Connected: 0,
      Disconnected: 0,
      Error: 0,
    }
    for (const conn of allConnectionsSummary) {
      counts[conn.status] += 1
    }
    return counts
  }, [allConnectionsSummary, totalConfigured])

  const hasActiveFilters = searchQuery.trim() !== '' || statusFilter !== 'All'

  const clearFilters = () => {
    setSearchQuery('')
    setDebouncedQuery('')
    setStatusFilter('All')
  }

  const handleCreateConnection = async (payload: CreateConnectionPayload) => {
    const created = await createConnection(payload)
    setSearchQuery('')
    setDebouncedQuery('')
    setStatusFilter('All')
    await fetchConnectionsFromApi({
      status: 'All',
      q: '',
      syncSummary: true,
    })
    setFeedbackBanner({
      title: `Connection "${created.name}" created.`,
      detail: `Persisted in backend storage with status ${created.status}.`,
    })
  }

  const handleStartEdit = (connection: Connection) => {
    setSelectedConnection(null)
    setTestingConnection(null)
    setIsAddModalOpen(false)
    setEditingConnection(connection)
  }

  const handleStartTest = (connection: Connection) => {
    setSelectedConnection(null)
    setIsAddModalOpen(false)
    setEditingConnection(null)
    setTestingConnection(connection)
  }

  const handleUpdateConnection = async (
    connectionId: string,
    payload: UpdateConnectionPayload,
  ) => {
    const updated = await updateConnection(connectionId, payload)
    setEditingConnection(null)
    await fetchConnectionsFromApi({ syncSummary: true })
    setFeedbackBanner({
      title: `Connection "${updated.name}" updated.`,
      detail: `Saved to backend database with status ${updated.status}.`,
    })
  }

  const handleConfirmDelete = async (connectionToDelete: Connection) => {
    if (isDeleting) return
    setIsDeleting(true)
    setDeleteError(null)

    try {
      await deleteConnection(connectionToDelete.id)
      setDeletingConnection(null)
      setSelectedConnection((prev) =>
        prev?.id === connectionToDelete.id ? null : prev,
      )
      setEditingConnection((prev) =>
        prev?.id === connectionToDelete.id ? null : prev,
      )
      setTestingConnection((prev) =>
        prev?.id === connectionToDelete.id ? null : prev,
      )
      await fetchConnectionsFromApi({ syncSummary: true })
      setFeedbackBanner({
        title: `Connection "${connectionToDelete.name}" deleted.`,
        detail:
          'Removed permanently along with its encrypted backend credentials.',
      })
    } catch (error) {
      if (error instanceof ConnectionApiClientError) {
        setDeleteError(error.message)
      } else {
        setDeleteError('Unable to delete connection. Please try again.')
      }
    } finally {
      setIsDeleting(false)
    }
  }

  const handleTestCompleted = () => {
    void fetchConnectionsFromApi({ syncSummary: true })
  }

  return (
    <div className="space-y-5">
      {/* Page header */}
      <div className="flex flex-col gap-4 border-b border-zinc-200/80 pb-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-xl font-semibold tracking-tight text-zinc-950 sm:text-2xl">
              Connections
            </h1>
            <span className="rounded border border-zinc-200 bg-zinc-100/80 px-2 py-0.5 text-[11px] font-medium text-zinc-700 tabular-nums">
              {totalConfigured} configured
            </span>
          </div>
          <p className="mt-1 text-sm text-zinc-500">
            Manage persisted source and destination connectors across databases,
            APIs, and file sources.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={isInitialLoading || isRefreshing}
            onClick={() => void fetchConnectionsFromApi({ syncSummary: true })}
            className="h-[34px]"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`}
              aria-hidden="true"
            />
            <span>Refresh</span>
          </Button>

          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              setEditingConnection(null)
              setIsAddModalOpen(true)
            }}
            className="h-[34px]"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Add Connection</span>
          </Button>
        </div>
      </div>

      {/* Creation / update / delete feedback banner */}
      {feedbackBanner && (
        <div
          role="status"
          className="flex items-start justify-between gap-3 rounded-lg border border-emerald-200/80 bg-emerald-50/70 px-4 py-3 text-xs text-emerald-950"
        >
          <div className="flex items-start gap-2.5">
            <CheckCircle2
              className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700"
              aria-hidden="true"
            />
            <div>
              <span className="font-semibold">{feedbackBanner.title}</span>{' '}
              {feedbackBanner.detail}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setFeedbackBanner(null)}
            aria-label="Dismiss status notice"
            className="cursor-pointer rounded p-0.5 text-emerald-700 hover:bg-emerald-100 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      )}

      {/* Search & Status Filter toolbar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center">
          <div className="relative w-full sm:w-72">
            <Search
              className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400"
              aria-hidden="true"
            />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search name, endpoint, or description…"
              aria-label="Search connections"
              className="h-8 w-full rounded-md border border-zinc-200 bg-white pr-7 pl-8 text-xs text-zinc-900 shadow-[0_1px_2px_rgba(15,23,42,0.03)] placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none"
            />
            {searchQuery !== '' && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                aria-label="Clear search query"
                className="absolute top-1/2 right-1.5 -translate-y-1/2 cursor-pointer rounded p-0.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            )}
          </div>

          <div
            className="flex flex-wrap items-center gap-1 rounded-md border border-zinc-200 bg-zinc-50 p-0.5"
            role="group"
            aria-label="Filter connections by status"
          >
            {STATUS_FILTERS.map((status) => {
              const active = statusFilter === status
              return (
                <button
                  key={status}
                  type="button"
                  onClick={() => setStatusFilter(status)}
                  aria-pressed={active}
                  className={`inline-flex cursor-pointer items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none ${
                    active
                      ? 'bg-white text-zinc-900 shadow-[0_1px_2px_rgba(15,23,42,0.06)]'
                      : 'text-zinc-500 hover:text-zinc-900'
                  }`}
                >
                  <span>{status}</span>
                  <span
                    className={`text-[11px] tabular-nums ${
                      active ? 'text-zinc-500' : 'text-zinc-400'
                    }`}
                  >
                    {statusCounts[status]}
                  </span>
                </button>
              )
            })}
          </div>
        </div>

        <div className="text-xs text-zinc-500 tabular-nums">
          Showing{' '}
          <span className="font-medium text-zinc-900">{connections.length}</span>{' '}
          of{' '}
          <span className="font-medium text-zinc-900">{totalFiltered}</span>{' '}
          connections
        </div>
      </div>

      {/* Loading / API Error / Empty / Card Grid */}
      {isInitialLoading ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center px-6 py-12 text-center">
            <Loader2
              className="h-6 w-6 animate-spin text-emerald-600"
              aria-hidden="true"
            />
            <h2 className="mt-3 text-sm font-semibold text-zinc-900">
              Loading connections…
            </h2>
            <p className="mt-1 text-xs text-zinc-500">
              Fetching persisted connections from the backend API.
            </p>
          </CardContent>
        </Card>
      ) : loadError ? (
        <Card>
          <CardContent className="px-6 py-12 text-center">
            <div role="alert" className="flex flex-col items-center justify-center">
              <span className="flex h-10 w-10 items-center justify-center rounded-full border border-rose-200 bg-rose-50 text-rose-600">
                <AlertCircle className="h-5 w-5" aria-hidden="true" />
              </span>
              <h2 className="mt-3 text-sm font-semibold text-zinc-900">
                Failed to load connections
              </h2>
              <p className="mt-1 max-w-md text-xs text-zinc-600">{loadError}</p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void fetchConnectionsFromApi({ syncSummary: true })}
                className="mt-4"
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Retry</span>
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : connections.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center px-6 py-12 text-center">
            <span className="flex h-10 w-10 items-center justify-center rounded-full border border-zinc-200 bg-zinc-50 text-zinc-500">
              <Cable className="h-5 w-5" aria-hidden="true" />
            </span>
            <h2 className="mt-3 text-sm font-semibold text-zinc-900">
              {hasActiveFilters
                ? 'No matching connections'
                : 'No connections configured'}
            </h2>
            <p className="mt-1 max-w-sm text-xs text-zinc-500">
              {hasActiveFilters
                ? 'No saved connectors match your current search or status filter. Try adjusting your criteria.'
                : 'Add your first PostgreSQL, MySQL, MongoDB, REST API, CSV, or JSON connector to persist it to the backend.'}
            </p>
            {hasActiveFilters ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={clearFilters}
                className="mt-4"
              >
                Clear filters
              </Button>
            ) : (
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  setEditingConnection(null)
                  setIsAddModalOpen(true)
                }}
                className="mt-4"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Add Connection</span>
              </Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {connections.map((connection) => (
            <ConnectionCard
              key={connection.id}
              connection={connection}
              onViewDetails={setSelectedConnection}
              onEdit={handleStartEdit}
              onTestConnection={handleStartTest}
              onDelete={(target) => {
                setDeleteError(null)
                setDeletingConnection(target)
              }}
            />
          ))}
        </div>
      )}

      <AddConnectionModal
        isOpen={isAddModalOpen}
        mode="create"
        onClose={() => setIsAddModalOpen(false)}
        onCreateConnection={handleCreateConnection}
      />

      <AddConnectionModal
        isOpen={editingConnection !== null}
        mode="edit"
        initialConnection={editingConnection}
        onClose={() => setEditingConnection(null)}
        onUpdateConnection={handleUpdateConnection}
      />

      <ConnectionDetailsModal
        connection={selectedConnection}
        onClose={() => setSelectedConnection(null)}
        onEdit={handleStartEdit}
        onTestConnection={handleStartTest}
      />

      <TestConnectionModal
        connection={testingConnection}
        onClose={() => setTestingConnection(null)}
        onTestCompleted={handleTestCompleted}
      />

      <DeleteConnectionDialog
        connection={deletingConnection}
        isDeleting={isDeleting}
        errorMessage={deleteError}
        onCancel={() => {
          if (!isDeleting) {
            setDeleteError(null)
            setDeletingConnection(null)
          }
        }}
        onConfirmDelete={(target) => void handleConfirmDelete(target)}
      />
    </div>
  )
}
