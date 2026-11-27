import {
  Database,
  Eye,
  FileText,
  FolderOpen,
  Globe,
  Layers,
  Loader2,
  Search,
  Table2,
  X,
} from 'lucide-react'
import type {
  SchemaObjectSummary,
  SchemaObjectType,
} from '../../types/schema.ts'
import { Badge } from '../ui/Badge.tsx'
import { Button } from '../ui/Button.tsx'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/Card.tsx'
import { formatObjectConfidence } from './discoveryForm.ts'

interface ObjectBrowserProps {
  objects: SchemaObjectSummary[]
  total: number
  limit: number
  offset: number
  selectedObjectId: string | null
  searchQuery: string
  objectTypeFilter: SchemaObjectType | 'All'
  isLoading: boolean
  error: string | null
  onSearchChange: (value: string) => void
  onTypeFilterChange: (type: SchemaObjectType | 'All') => void
  onSelectObject: (object: SchemaObjectSummary) => void
  onPageChange: (nextOffset: number) => void
  onRetry: () => void
}

const OBJECT_TYPE_FILTERS: Array<{
  value: SchemaObjectType | 'All'
  label: string
}> = [
  { value: 'All', label: 'All' },
  { value: 'table', label: 'Tables' },
  { value: 'view', label: 'Views' },
  { value: 'collection', label: 'Collections' },
  { value: 'endpoint', label: 'Endpoints' },
  { value: 'file', label: 'Files' },
  { value: 'record_set', label: 'Record Sets' },
]

function ObjectTypeIcon({ type }: { type: SchemaObjectType }) {
  const cls = 'h-3.5 w-3.5 shrink-0'
  switch (type) {
    case 'table':
      return <Table2 className={cls} aria-hidden="true" />
    case 'view':
      return <Eye className={cls} aria-hidden="true" />
    case 'collection':
      return <Layers className={cls} aria-hidden="true" />
    case 'endpoint':
      return <Globe className={cls} aria-hidden="true" />
    case 'file':
      return <FileText className={cls} aria-hidden="true" />
    case 'record_set':
      return <Database className={cls} aria-hidden="true" />
  }
}

export function ObjectBrowser({
  objects,
  total,
  limit,
  offset,
  selectedObjectId,
  searchQuery,
  objectTypeFilter,
  isLoading,
  error,
  onSearchChange,
  onTypeFilterChange,
  onSelectObject,
  onPageChange,
  onRetry,
}: ObjectBrowserProps) {
  const hasFilters = searchQuery.trim() !== '' || objectTypeFilter !== 'All'
  const rangeStart = total === 0 ? 0 : offset + 1
  const rangeEnd = Math.min(offset + objects.length, total)
  const canPrev = offset > 0
  const canNext = offset + limit < total

  return (
    <Card className="flex flex-col dark:border-zinc-800 dark:bg-zinc-900">
      <CardHeader className="px-4 py-3 dark:border-zinc-800">
        <div className="flex w-full items-center justify-between gap-2">
          <CardTitle className="dark:text-zinc-100">Objects</CardTitle>
          <span className="text-[11px] text-zinc-500 tabular-nums dark:text-zinc-400">
            {total} total
          </span>
        </div>

        <div className="relative mt-2 w-full">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400"
            aria-hidden="true"
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search object name or key…"
            aria-label="Search discovered objects"
            className="h-8 w-full rounded-md border border-zinc-200 bg-white pr-7 pl-8 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
          />
          {searchQuery !== '' && (
            <button
              type="button"
              onClick={() => onSearchChange('')}
              aria-label="Clear object search"
              className="absolute top-1/2 right-1.5 -translate-y-1/2 cursor-pointer rounded p-0.5 text-zinc-400 hover:text-zinc-700"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          )}
        </div>

        <div
          className="flex flex-wrap gap-1 pt-1"
          role="group"
          aria-label="Filter objects by type"
        >
          {OBJECT_TYPE_FILTERS.map((filter) => {
            const active = objectTypeFilter === filter.value
            return (
              <button
                key={filter.value}
                type="button"
                aria-pressed={active}
                onClick={() => onTypeFilterChange(filter.value)}
                className={`cursor-pointer rounded px-2 py-0.5 text-[11px] font-medium transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none ${
                  active
                    ? 'bg-emerald-600 text-white'
                    : 'bg-zinc-100 text-zinc-600 hover:bg-zinc-200/80 hover:text-zinc-900 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700'
                }`}
              >
                {filter.label}
              </button>
            )
          })}
        </div>
      </CardHeader>

      <CardContent className="flex-1 p-2">
        {isLoading ? (
          <div className="flex items-center justify-center py-10 text-xs text-zinc-500">
            <Loader2
              className="mr-2 h-4 w-4 animate-spin text-emerald-600"
              aria-hidden="true"
            />
            <span>Loading objects…</span>
          </div>
        ) : error ? (
          <div role="alert" className="px-3 py-8 text-center">
            <p className="text-xs text-rose-600">{error}</p>
            <Button
              variant="secondary"
              size="sm"
              onClick={onRetry}
              className="mt-3"
            >
              Retry
            </Button>
          </div>
        ) : objects.length === 0 ? (
          <div className="flex flex-col items-center justify-center px-3 py-10 text-center">
            <FolderOpen
              className="h-6 w-6 text-zinc-400"
              aria-hidden="true"
            />
            <p className="mt-2 text-xs font-medium text-zinc-800 dark:text-zinc-200">
              {hasFilters
                ? 'No objects match current filters'
                : 'No schema objects discovered'}
            </p>
            {hasFilters && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  onSearchChange('')
                  onTypeFilterChange('All')
                }}
                className="mt-3"
              >
                Clear filters
              </Button>
            )}
          </div>
        ) : (
          <ul
            role="listbox"
            aria-label="Discovered schema objects"
            className="space-y-1"
          >
            {objects.map((obj) => {
              const selected = obj.id === selectedObjectId
              const confidence = formatObjectConfidence(obj.canonicalMetadata)
              return (
                <li key={obj.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={selected}
                    onClick={() => onSelectObject(obj)}
                    className={`w-full cursor-pointer rounded-md border px-3 py-2 text-left transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none ${
                      selected
                        ? 'border-emerald-300 bg-emerald-50/70 text-zinc-900 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-zinc-100'
                        : 'border-transparent hover:border-zinc-200 hover:bg-zinc-50 dark:hover:border-zinc-800 dark:hover:bg-zinc-800/50'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="text-zinc-500 dark:text-zinc-400">
                          <ObjectTypeIcon type={obj.objectType} />
                        </span>
                        <span className="truncate text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                          {obj.objectName}
                        </span>
                      </div>
                      <Badge tone="zinc" className="px-1.5 py-0 text-[10px]">
                        {obj.objectType}
                      </Badge>
                    </div>
                    <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-zinc-500 dark:text-zinc-400">
                      <span className="truncate font-mono">{obj.objectKey}</span>
                      <span className="shrink-0 tabular-nums">
                        {obj.fieldCount} fields
                        {confidence ? ` · ${confidence}` : ''}
                      </span>
                    </div>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>

      {total > limit && (
        <div className="flex items-center justify-between border-t border-zinc-100 px-3 py-2 text-[11px] text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          <span className="tabular-nums">
            {rangeStart}–{rangeEnd} of {total}
          </span>
          <div className="flex items-center gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              disabled={!canPrev || isLoading}
              onClick={() => onPageChange(Math.max(0, offset - limit))}
              className="h-7 px-2 text-[11px]"
            >
              Prev
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={!canNext || isLoading}
              onClick={() => onPageChange(offset + limit)}
              className="h-7 px-2 text-[11px]"
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </Card>
  )
}
