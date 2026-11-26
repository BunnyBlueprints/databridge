import { useMemo, useState } from 'react'
import { Cable, CheckCircle2, Plus, Search, X } from 'lucide-react'
import { AddConnectionModal } from '../components/connections/AddConnectionModal'
import { ConnectionCard } from '../components/connections/ConnectionCard'
import { ConnectionDetailsModal } from '../components/connections/ConnectionDetailsModal'
import { DeleteConnectionDialog } from '../components/connections/DeleteConnectionDialog'
import { Button } from '../components/ui/Button'
import { Card, CardContent } from '../components/ui/Card'
import { MOCK_CONNECTIONS } from '../data/mockConnections'
import type { Connection, ConnectionStatus } from '../types/connection'

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
  const [connections, setConnections] = useState<Connection[]>(() => [
    ...MOCK_CONNECTIONS,
  ])
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('All')
  const [isAddModalOpen, setIsAddModalOpen] = useState(false)
  const [editingConnection, setEditingConnection] =
    useState<Connection | null>(null)
  const [selectedConnection, setSelectedConnection] =
    useState<Connection | null>(null)
  const [deletingConnection, setDeletingConnection] =
    useState<Connection | null>(null)
  const [feedbackBanner, setFeedbackBanner] = useState<FeedbackBanner | null>(
    null,
  )

  const statusCounts = useMemo(() => {
    const counts: Record<StatusFilter, number> = {
      All: connections.length,
      Connected: 0,
      Disconnected: 0,
      Error: 0,
    }
    for (const conn of connections) {
      counts[conn.status] += 1
    }
    return counts
  }, [connections])

  const filteredConnections = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()

    return connections.filter((connection) => {
      if (statusFilter !== 'All' && connection.status !== statusFilter) {
        return false
      }
      if (!query) return true

      return (
        connection.name.toLowerCase().includes(query) ||
        connection.type.toLowerCase().includes(query) ||
        connection.endpoint.toLowerCase().includes(query)
      )
    })
  }, [connections, searchQuery, statusFilter])

  const hasActiveFilters = searchQuery.trim() !== '' || statusFilter !== 'All'

  const clearFilters = () => {
    setSearchQuery('')
    setStatusFilter('All')
  }

  const handleCreateConnection = (newConnection: Connection) => {
    setConnections((prev) => [newConnection, ...prev])
    setSearchQuery('')
    setStatusFilter('All')
    setFeedbackBanner({
      title: `Connection "${newConnection.name}" added.`,
      detail:
        'Saved in local session state with status Disconnected (not tested or persisted to a backend).',
    })
  }

  const handleStartEdit = (connection: Connection) => {
    setSelectedConnection(null)
    setIsAddModalOpen(false)
    setEditingConnection(connection)
  }

  const handleUpdateConnection = (updatedConnection: Connection) => {
    setConnections((prev) =>
      prev.map((item) =>
        item.id === updatedConnection.id ? updatedConnection : item,
      ),
    )
    setEditingConnection(null)
    setFeedbackBanner({
      title: `Connection "${updatedConnection.name}" updated.`,
      detail: `Changes saved in local session state with status ${updatedConnection.status} preserved.`,
    })
  }

  const handleConfirmDelete = (connectionToDelete: Connection) => {
    setConnections((prev) =>
      prev.filter((item) => item.id !== connectionToDelete.id),
    )
    setDeletingConnection(null)
    setSelectedConnection((prev) =>
      prev?.id === connectionToDelete.id ? null : prev,
    )
    setEditingConnection((prev) =>
      prev?.id === connectionToDelete.id ? null : prev,
    )
    setFeedbackBanner({
      title: `Connection "${connectionToDelete.name}" removed.`,
      detail:
        'Deleted from current application state (mock dataset and external sources remain unchanged).',
    })
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
              {connections.length} configured
            </span>
            <span className="rounded border border-zinc-200/80 bg-white px-1.5 py-0.5 text-[11px] text-zinc-500">
              Prototype · Mock Data
            </span>
          </div>
          <p className="mt-1 text-sm text-zinc-500">
            Manage source and destination connectors across databases, APIs, and
            storage.
          </p>
        </div>

        <div className="flex items-center gap-2">
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

      {/* Creation / update feedback banner */}
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
              placeholder="Search name, type, or endpoint…"
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
          <span className="font-medium text-zinc-900">
            {filteredConnections.length}
          </span>{' '}
          of{' '}
          <span className="font-medium text-zinc-900">
            {connections.length}
          </span>{' '}
          connections
        </div>
      </div>

      {/* Responsive card grid or empty state */}
      {filteredConnections.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center px-6 py-12 text-center">
            <span className="flex h-10 w-10 items-center justify-center rounded-full border border-zinc-200 bg-zinc-50 text-zinc-500">
              <Cable className="h-5 w-5" aria-hidden="true" />
            </span>
            <h2 className="mt-3 text-sm font-semibold text-zinc-900">
              No matching connections
            </h2>
            <p className="mt-1 max-w-sm text-xs text-zinc-500">
              No configured connectors match your current search or status
              filter. Try adjusting your criteria.
            </p>
            {hasActiveFilters && (
              <Button
                variant="secondary"
                size="sm"
                onClick={clearFilters}
                className="mt-4"
              >
                Clear filters
              </Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filteredConnections.map((connection) => (
            <ConnectionCard
              key={connection.id}
              connection={connection}
              onViewDetails={setSelectedConnection}
              onEdit={handleStartEdit}
              onDelete={setDeletingConnection}
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
      />

      <DeleteConnectionDialog
        connection={deletingConnection}
        onCancel={() => setDeletingConnection(null)}
        onConfirmDelete={handleConfirmDelete}
      />
    </div>
  )
}
