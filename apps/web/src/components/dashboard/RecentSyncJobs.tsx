import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  ArrowUpDown,
  Info,
  RotateCcw,
  Search,
  X,
} from 'lucide-react'
import type { SyncJob, SyncJobStatus } from '../../types/dashboard'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import { Button } from '../ui/Button'
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from '../ui/Card'
import { ConnectorIcon } from '../ui/ConnectorIcon'

interface RecentSyncJobsProps {
  jobs: SyncJob[]
}

type StatusFilter = 'All' | SyncJobStatus
type SortField = 'name' | 'records' | 'duration' | 'lastRun'
type SortDirection = 'asc' | 'desc'

const STATUS_FILTERS: StatusFilter[] = [
  'All',
  'Success',
  'Failed',
  'Running',
  'Pending',
]

function getStatusBadgeProps(status: SyncJobStatus): {
  tone: BadgeTone
  dot: boolean
  pulseDot?: boolean
} {
  switch (status) {
    case 'Success':
      return { tone: 'emerald', dot: true }
    case 'Failed':
      return { tone: 'rose', dot: true }
    case 'Running':
      return { tone: 'amber', dot: true, pulseDot: true }
    case 'Pending':
      return { tone: 'zinc', dot: true }
  }
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—'
  if (seconds < 60) return `${seconds}s`
  const mins = Math.floor(seconds / 60)
  const rem = seconds % 60
  return `${mins}m ${rem}s`
}

export function RecentSyncJobs({ jobs }: RecentSyncJobsProps) {
  const navigate = useNavigate()
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('All')
  const [sortField, setSortField] = useState<SortField>('lastRun')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')
  const [mockRetriedIds, setMockRetriedIds] = useState<Record<string, boolean>>(
    {},
  )
  const [retryNoticeJob, setRetryNoticeJob] = useState<SyncJob | null>(null)

  const filteredAndSortedJobs = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()

    const filtered = jobs.filter((job) => {
      if (statusFilter !== 'All' && job.status !== statusFilter) {
        return false
      }
      if (!q) return true
      return (
        job.name.toLowerCase().includes(q) ||
        job.sourceName.toLowerCase().includes(q) ||
        job.destinationName.toLowerCase().includes(q) ||
        job.sourceType.toLowerCase().includes(q) ||
        job.destinationType.toLowerCase().includes(q)
      )
    })

    const sorted = [...filtered].sort((a, b) => {
      let cmp = 0
      switch (sortField) {
        case 'name':
          cmp = a.name.localeCompare(b.name)
          break
        case 'records':
          cmp = a.recordsProcessed - b.recordsProcessed
          break
        case 'duration':
          cmp = (a.durationSeconds ?? -1) - (b.durationSeconds ?? -1)
          break
        case 'lastRun':
          cmp = a.lastRunTimestamp - b.lastRunTimestamp
          break
      }
      return sortDirection === 'asc' ? cmp : -cmp
    })

    return sorted
  }, [jobs, searchQuery, statusFilter, sortField, sortDirection])

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDirection(field === 'name' ? 'asc' : 'desc')
    }
  }

  const renderSortIcon = (field: SortField) => {
    if (sortField !== field) {
      return <ArrowUpDown className="h-3 w-3 text-zinc-400" aria-hidden="true" />
    }
    return sortDirection === 'asc' ? (
      <ArrowUp className="h-3 w-3 text-zinc-800" aria-hidden="true" />
    ) : (
      <ArrowDown className="h-3 w-3 text-zinc-800" aria-hidden="true" />
    )
  }

  const handleMockRetry = (job: SyncJob) => {
    setMockRetriedIds((prev) => ({ ...prev, [job.id]: true }))
    setRetryNoticeJob(job)
  }

  const clearFilters = () => {
    setSearchQuery('')
    setStatusFilter('All')
  }

  return (
    <Card>
      <CardHeader className="gap-3">
        <div>
          <CardTitle>Recent Sync Jobs</CardTitle>
          <CardDescription>
            Latest pipeline executions across workspace connectors
          </CardDescription>
        </div>

        <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-center">
          {/* Search input */}
          <div className="relative w-full sm:w-56">
            <Search
              className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400"
              aria-hidden="true"
            />
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search jobs or connectors…"
              aria-label="Search sync jobs"
              className="h-8 w-full rounded-md border border-zinc-200 bg-white pr-2.5 pl-8 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none"
            />
          </div>

          {/* Status filter */}
          <div
            className="flex flex-wrap items-center gap-1 rounded-md border border-zinc-200 bg-zinc-50 p-0.5"
            role="group"
            aria-label="Filter sync jobs by status"
          >
            {STATUS_FILTERS.map((status) => {
              const active = statusFilter === status
              return (
                <button
                  key={status}
                  type="button"
                  onClick={() => setStatusFilter(status)}
                  aria-pressed={active}
                  className={`cursor-pointer rounded px-2 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none ${
                    active
                      ? 'bg-white text-zinc-900 shadow-[0_1px_2px_rgba(15,23,42,0.06)]'
                      : 'text-zinc-500 hover:text-zinc-900'
                  }`}
                >
                  {status}
                </button>
              )
            })}
          </div>
        </div>
      </CardHeader>

      {/* Isolated mock retry explanation banner */}
      {retryNoticeJob && (
        <div
          role="status"
          className="flex items-start justify-between gap-3 border-b border-amber-200/80 bg-amber-50/70 px-5 py-2.5 text-xs text-amber-900"
        >
          <div className="flex items-start gap-2">
            <Info
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700"
              aria-hidden="true"
            />
            <span>
              <strong>Prototype action:</strong> Retry for{' '}
              <code className="rounded bg-amber-100/80 px-1 py-0.5 font-mono text-[11px]">
                {retryNoticeJob.name}
              </code>{' '}
              is mocked on the frontend. Live job execution requires backend
              worker integration.
            </span>
          </div>
          <button
            type="button"
            onClick={() => setRetryNoticeJob(null)}
            aria-label="Dismiss retry notice"
            className="cursor-pointer rounded p-0.5 text-amber-700 hover:bg-amber-100 focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:outline-none"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      )}

      <div className="overflow-x-auto">
        {filteredAndSortedJobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
            <p className="text-sm font-medium text-zinc-900">
              No matching sync jobs
            </p>
            <p className="mt-1 text-xs text-zinc-500">
              Adjust your search query or status filter to view pipeline runs.
            </p>
            <Button
              variant="secondary"
              size="sm"
              onClick={clearFilters}
              className="mt-4"
            >
              Clear filters
            </Button>
          </div>
        ) : (
          <table className="w-full min-w-[740px] border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-200/80 bg-zinc-50/70 text-[11px] font-medium tracking-wide text-zinc-500 uppercase">
                <th className="px-5 py-2.5">
                  <button
                    type="button"
                    onClick={() => handleSort('name')}
                    className="inline-flex cursor-pointer items-center gap-1 hover:text-zinc-900 focus-visible:underline focus-visible:outline-none"
                  >
                    Job name
                    {renderSortIcon('name')}
                  </button>
                </th>
                <th className="px-4 py-2.5">Source → Destination</th>
                <th className="px-4 py-2.5">Status</th>
                <th className="px-4 py-2.5 text-right">
                  <button
                    type="button"
                    onClick={() => handleSort('records')}
                    className="inline-flex cursor-pointer items-center gap-1 hover:text-zinc-900 focus-visible:underline focus-visible:outline-none"
                  >
                    Records
                    {renderSortIcon('records')}
                  </button>
                </th>
                <th className="px-4 py-2.5 text-right">
                  <button
                    type="button"
                    onClick={() => handleSort('duration')}
                    className="inline-flex cursor-pointer items-center gap-1 hover:text-zinc-900 focus-visible:underline focus-visible:outline-none"
                  >
                    Duration
                    {renderSortIcon('duration')}
                  </button>
                </th>
                <th className="px-4 py-2.5 text-right">
                  <button
                    type="button"
                    onClick={() => handleSort('lastRun')}
                    className="inline-flex cursor-pointer items-center gap-1 hover:text-zinc-900 focus-visible:underline focus-visible:outline-none"
                  >
                    Last run
                    {renderSortIcon('lastRun')}
                  </button>
                </th>
                <th className="px-5 py-2.5 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100">
              {filteredAndSortedJobs.map((job) => {
                const badgeProps = getStatusBadgeProps(job.status)
                const isMockRetried = Boolean(mockRetriedIds[job.id])

                return (
                  <tr
                    key={job.id}
                    onClick={() => navigate(`/sync-jobs/${job.id}`)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        navigate(`/sync-jobs/${job.id}`)
                      }
                    }}
                    tabIndex={0}
                    className="cursor-pointer transition-colors hover:bg-zinc-50/90 focus-visible:bg-emerald-50/40 focus-visible:outline-none"
                  >
                    <td className="px-5 py-3">
                      <div className="font-mono text-xs font-medium text-zinc-900">
                        {job.name}
                      </div>
                      <div className="mt-0.5 text-[11px] text-zinc-500">
                        {job.schedule}
                      </div>
                    </td>

                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2 text-zinc-700">
                        <span className="inline-flex items-center gap-1.5">
                          <ConnectorIcon type={job.sourceType} size="sm" />
                          <span className="font-medium text-zinc-800">
                            {job.sourceName}
                          </span>
                        </span>
                        <ArrowRight
                          className="h-3 w-3 shrink-0 text-zinc-400"
                          aria-hidden="true"
                        />
                        <span className="inline-flex items-center gap-1.5">
                          <ConnectorIcon type={job.destinationType} size="sm" />
                          <span className="font-medium text-zinc-800">
                            {job.destinationName}
                          </span>
                        </span>
                      </div>
                    </td>

                    <td className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge
                          tone={badgeProps.tone}
                          dot={badgeProps.dot}
                          pulseDot={badgeProps.pulseDot}
                        >
                          {job.status}
                        </Badge>
                        {isMockRetried && (
                          <span className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
                            Mock retry noted
                          </span>
                        )}
                      </div>
                    </td>

                    <td className="px-4 py-3 text-right font-medium text-zinc-900 tabular-nums">
                      {job.recordsProcessed.toLocaleString()}
                    </td>

                    <td className="px-4 py-3 text-right text-zinc-600 tabular-nums">
                      {formatDuration(job.durationSeconds)}
                    </td>

                    <td className="px-4 py-3 text-right text-zinc-500 tabular-nums">
                      {job.lastRunLabel}
                    </td>

                    <td className="px-5 py-3 text-right">
                      {job.status === 'Failed' ? (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleMockRetry(job)
                          }}
                          title="Simulate retry (backend integration pending)"
                          className="inline-flex cursor-pointer items-center gap-1 rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium text-zinc-700 transition-colors hover:border-zinc-300 hover:bg-zinc-50 hover:text-zinc-900 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none"
                        >
                          <RotateCcw className="h-3 w-3" aria-hidden="true" />
                          {isMockRetried ? 'Retried (mock)' : 'Retry'}
                        </button>
                      ) : (
                        <span className="text-[11px] font-medium text-zinc-400">
                          View
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </Card>
  )
}
