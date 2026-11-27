import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Compass,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Workflow,
  X,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import type { Connection } from '../../types/connection.ts'
import type { MappingRecord, MappingStatus } from '../../types/mapping.ts'
import { Badge } from '../ui/Badge.tsx'
import { Button } from '../ui/Button.tsx'
import { Card, CardContent, CardHeader } from '../ui/Card.tsx'
import { ConnectorIcon } from '../ui/ConnectorIcon.tsx'
import {
  formatMappingTimestamp,
  getMappingStatusDisplay,
} from './mappingHelpers.ts'

export type MappingStatusFilter = 'All' | MappingStatus

interface MappingListViewProps {
  mappings: MappingRecord[]
  total: number
  limit: number
  offset: number
  connections: Connection[]
  searchQuery: string
  statusFilter: MappingStatusFilter
  sourceConnectionFilter: string
  destinationConnectionFilter: string
  isLoading: boolean
  isRefreshing: boolean
  error: string | null
  onSearchChange: (value: string) => void
  onStatusFilterChange: (status: MappingStatusFilter) => void
  onSourceConnectionFilterChange: (connectionId: string) => void
  onDestinationConnectionFilterChange: (connectionId: string) => void
  onClearFilters: () => void
  onPageChange: (nextOffset: number) => void
  onRefresh: () => void
  onCreateNew: () => void
  onOpenMapping: (mapping: MappingRecord) => void
  onDeleteRequest: (mapping: MappingRecord) => void
}

const STATUS_FILTERS: Array<{ value: MappingStatusFilter; label: string }> = [
  { value: 'All', label: 'All statuses' },
  { value: 'draft', label: 'Draft' },
  { value: 'valid', label: 'Valid' },
  { value: 'invalid', label: 'Invalid' },
]

export function MappingListView({
  mappings,
  total,
  limit,
  offset,
  connections,
  searchQuery,
  statusFilter,
  sourceConnectionFilter,
  destinationConnectionFilter,
  isLoading,
  isRefreshing,
  error,
  onSearchChange,
  onStatusFilterChange,
  onSourceConnectionFilterChange,
  onDestinationConnectionFilterChange,
  onClearFilters,
  onPageChange,
  onRefresh,
  onCreateNew,
  onOpenMapping,
  onDeleteRequest,
}: MappingListViewProps) {
  const connectionById = new Map(connections.map((c) => [c.id, c]))
  const hasFilters =
    searchQuery.trim() !== '' ||
    statusFilter !== 'All' ||
    sourceConnectionFilter !== '' ||
    destinationConnectionFilter !== ''

  const rangeStart = total === 0 ? 0 : offset + 1
  const rangeEnd = Math.min(offset + mappings.length, total)
  const canPrev = offset > 0
  const canNext = offset + limit < total

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-zinc-950 dark:text-zinc-50">
            Mapping Studio
          </h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Define ordered field mappings between discovered source and
            destination objects and validate them against backend schemas.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={onRefresh}
            disabled={isLoading || isRefreshing}
            className="dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`}
              aria-hidden="true"
            />
            <span>Refresh</span>
          </Button>
          <Button variant="primary" size="sm" onClick={onCreateNew}>
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            <span>New Mapping</span>
          </Button>
        </div>
      </div>

      <Card className="dark:border-zinc-800 dark:bg-zinc-900">
        <CardHeader className="px-4 py-3 dark:border-zinc-800">
          <div className="grid w-full grid-cols-1 gap-2.5 md:grid-cols-2 lg:grid-cols-4">
            <div className="relative">
              <Search
                className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400"
                aria-hidden="true"
              />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => onSearchChange(e.target.value)}
                placeholder="Search mapping name or object…"
                aria-label="Search mappings"
                className="h-8 w-full rounded-md border border-zinc-200 bg-white pr-7 pl-8 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
              />
              {searchQuery !== '' && (
                <button
                  type="button"
                  onClick={() => onSearchChange('')}
                  aria-label="Clear search query"
                  className="absolute top-1/2 right-1.5 -translate-y-1/2 cursor-pointer rounded p-0.5 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              )}
            </div>

            <select
              aria-label="Filter by source connection"
              value={sourceConnectionFilter}
              onChange={(e) => onSourceConnectionFilterChange(e.target.value)}
              className="h-8 rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
            >
              <option value="">All source connections</option>
              {connections.map((conn) => (
                <option key={conn.id} value={conn.id}>
                  Source: {conn.name} ({conn.type})
                </option>
              ))}
            </select>

            <select
              aria-label="Filter by destination connection"
              value={destinationConnectionFilter}
              onChange={(e) =>
                onDestinationConnectionFilterChange(e.target.value)
              }
              className="h-8 rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
            >
              <option value="">All destination connections</option>
              {connections.map((conn) => (
                <option key={conn.id} value={conn.id}>
                  Dest: {conn.name} ({conn.type})
                </option>
              ))}
            </select>

            <div className="flex items-center gap-2">
              <select
                aria-label="Filter by mapping status"
                value={statusFilter}
                onChange={(e) =>
                  onStatusFilterChange(e.target.value as MappingStatusFilter)
                }
                className="h-8 flex-1 rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
              >
                {STATUS_FILTERS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              {hasFilters && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onClearFilters}
                  className="text-xs dark:text-zinc-300 dark:hover:bg-zinc-800"
                >
                  Clear
                </Button>
              )}
            </div>
          </div>
        </CardHeader>

        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, idx) => (
                <div
                  key={idx}
                  className="h-14 animate-pulse rounded-md bg-zinc-100 dark:bg-zinc-800/70"
                />
              ))}
            </div>
          ) : error ? (
            <div
              role="alert"
              className="flex flex-col items-center justify-center px-6 py-14 text-center"
            >
              <AlertCircle
                className="h-7 w-7 text-rose-500"
                aria-hidden="true"
              />
              <p className="mt-2.5 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Unable to load mappings
              </p>
              <p className="mt-1 max-w-md text-xs text-zinc-500 dark:text-zinc-400">
                {error}
              </p>
              <Button
                variant="secondary"
                size="sm"
                onClick={onRefresh}
                className="mt-4"
              >
                Retry
              </Button>
            </div>
          ) : mappings.length === 0 ? (
            <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-zinc-200 bg-zinc-50 text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
                <Workflow className="h-5 w-5" aria-hidden="true" />
              </div>
              <p className="mt-3 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                {hasFilters
                  ? 'No mappings match your filters'
                  : 'No field mappings created yet'}
              </p>
              <p className="mt-1 max-w-md text-xs text-zinc-500 dark:text-zinc-400">
                {hasFilters
                  ? 'Adjust or clear your search query and connection/status filters to see all saved mappings.'
                  : 'Discover source and destination schemas in Data Explorer, then create your first persistent field mapping.'}
              </p>
              <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
                {hasFilters ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={onClearFilters}
                  >
                    Clear Filters
                  </Button>
                ) : (
                  <>
                    <Link
                      to="/data-explorer"
                      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-3 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
                    >
                      <Compass className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>Open Data Explorer</span>
                    </Link>
                    <Button variant="primary" size="sm" onClick={onCreateNew}>
                      <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>Create First Mapping</span>
                    </Button>
                  </>
                )}
              </div>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-zinc-200/80 text-left text-xs dark:divide-zinc-800">
                <thead className="bg-zinc-50/80 text-[11px] font-semibold tracking-wider text-zinc-500 uppercase dark:bg-zinc-950/60 dark:text-zinc-400">
                  <tr>
                    <th className="px-4 py-2.5">Mapping</th>
                    <th className="px-4 py-2.5">Source → Destination</th>
                    <th className="px-4 py-2.5">Status</th>
                    <th className="px-4 py-2.5">Rules</th>
                    <th className="px-4 py-2.5">Version</th>
                    <th className="px-4 py-2.5">Updated</th>
                    <th className="px-4 py-2.5 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-200/70 bg-white dark:divide-zinc-800/80 dark:bg-zinc-900">
                  {mappings.map((mapping) => {
                    const srcConn = connectionById.get(
                      mapping.sourceConnectionId,
                    )
                    const dstConn = connectionById.get(
                      mapping.destinationConnectionId,
                    )
                    const statusDisplay = getMappingStatusDisplay(
                      mapping.status,
                    )
                    const drift =
                      mapping.validationSummary?.schemaDriftDetected === true

                    return (
                      <tr
                        key={mapping.id}
                        className="transition-colors hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40"
                      >
                        <td className="max-w-xs px-4 py-3 align-top">
                          <button
                            type="button"
                            onClick={() => onOpenMapping(mapping)}
                            className="cursor-pointer text-left font-semibold text-zinc-900 hover:text-emerald-700 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none dark:text-zinc-100 dark:hover:text-emerald-400"
                          >
                            {mapping.name}
                          </button>
                          {mapping.description && (
                            <p className="mt-0.5 line-clamp-1 text-[11px] text-zinc-500 dark:text-zinc-400">
                              {mapping.description}
                            </p>
                          )}
                        </td>

                        <td className="px-4 py-3 align-top">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="inline-flex items-center gap-1.5 font-medium text-zinc-800 dark:text-zinc-200">
                              {srcConn && (
                                <ConnectorIcon type={srcConn.type} size="sm" />
                              )}
                              <span>
                                {srcConn?.name ?? 'Unknown connection'}
                              </span>
                            </span>
                            <ArrowRight
                              className="h-3 w-3 text-zinc-400"
                              aria-hidden="true"
                            />
                            <span className="inline-flex items-center gap-1.5 font-medium text-zinc-800 dark:text-zinc-200">
                              {dstConn && (
                                <ConnectorIcon type={dstConn.type} size="sm" />
                              )}
                              <span>
                                {dstConn?.name ?? 'Unknown connection'}
                              </span>
                            </span>
                          </div>
                          <div className="mt-1 font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
                            {mapping.sourceObjectKey} →{' '}
                            {mapping.destinationObjectKey}
                          </div>
                        </td>

                        <td className="px-4 py-3 align-top">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <Badge tone={statusDisplay.tone} dot>
                              {statusDisplay.label}
                            </Badge>
                            {drift && (
                              <Badge tone="amber">
                                <AlertTriangle
                                  className="h-3 w-3"
                                  aria-hidden="true"
                                />
                                <span>Drift</span>
                              </Badge>
                            )}
                          </div>
                        </td>

                        <td className="px-4 py-3 align-top tabular-nums text-zinc-700 dark:text-zinc-300">
                          {mapping.rules.length}
                        </td>

                        <td className="px-4 py-3 align-top font-mono text-[11px] text-zinc-600 dark:text-zinc-400">
                          v{mapping.mappingVersion}
                        </td>

                        <td className="px-4 py-3 align-top whitespace-nowrap text-zinc-500 dark:text-zinc-400">
                          {formatMappingTimestamp(mapping.updatedAt)}
                        </td>

                        <td className="px-4 py-3 text-right align-top">
                          <div className="inline-flex items-center justify-end gap-1.5">
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => onOpenMapping(mapping)}
                              className="h-7 px-2.5 text-[11px] dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
                            >
                              <Pencil
                                className="h-3 w-3"
                                aria-hidden="true"
                              />
                              <span>Edit</span>
                            </Button>
                            <button
                              type="button"
                              onClick={() => onDeleteRequest(mapping)}
                              aria-label={`Delete mapping ${mapping.name}`}
                              className="inline-flex h-7 cursor-pointer items-center gap-1 rounded-md border border-zinc-200 bg-white px-2 text-[11px] font-medium text-rose-600 transition-colors hover:border-rose-200 hover:bg-rose-50 focus-visible:ring-2 focus-visible:ring-rose-600 focus-visible:outline-none dark:border-zinc-700 dark:bg-zinc-800 dark:text-rose-400 dark:hover:border-rose-900 dark:hover:bg-rose-950/40"
                            >
                              <Trash2
                                className="h-3 w-3"
                                aria-hidden="true"
                              />
                              <span>Delete</span>
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>

        {total > 0 && !isLoading && !error && (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-200/80 px-4 py-2.5 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
            <span className="tabular-nums">
              Showing {rangeStart}–{rangeEnd} of {total} mapping
              {total === 1 ? '' : 's'}
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={!canPrev}
                onClick={() => onPageChange(Math.max(0, offset - limit))}
                className="h-7 px-2.5 text-[11px] dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={!canNext}
                onClick={() => onPageChange(offset + limit)}
                className="h-7 px-2.5 text-[11px] dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
