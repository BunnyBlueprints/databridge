import { useNavigate } from 'react-router-dom'
import { Calendar, Plus, RefreshCw } from 'lucide-react'
import { DATE_RANGE_OPTIONS } from '../../data/mockDashboardData'
import type { DateRangeKey } from '../../types/dashboard'
import { Button } from '../ui/Button'

interface DashboardHeaderProps {
  dateRange: DateRangeKey
  onDateRangeChange: (range: DateRangeKey) => void
  onRefresh: () => void
  isRefreshing: boolean
  lastRefreshedLabel: string
  workspaceName: string
}

export function DashboardHeader({
  dateRange,
  onDateRangeChange,
  onRefresh,
  isRefreshing,
  lastRefreshedLabel,
  workspaceName,
}: DashboardHeaderProps) {
  const navigate = useNavigate()

  return (
    <div className="flex flex-col gap-4 border-b border-zinc-200/80 pb-5 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-xl font-semibold tracking-tight text-zinc-950 sm:text-2xl">
            Overview
          </h1>
          <span className="rounded border border-zinc-200 bg-zinc-100/80 px-1.5 py-0.5 text-[11px] font-medium text-zinc-600">
            {workspaceName}
          </span>
          <span className="rounded border border-zinc-200/80 bg-white px-1.5 py-0.5 text-[11px] text-zinc-500">
            Prototype · Mock Data
          </span>
        </div>
        <p className="mt-1 text-sm text-zinc-500">
          Monitor your integrations and data pipeline health.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {/* Date range selector */}
        <div
          className="inline-flex items-center rounded-md border border-zinc-200 bg-white p-0.5 shadow-[0_1px_2px_rgba(15,23,42,0.03)]"
          role="group"
          aria-label="Dashboard date range"
        >
          <span className="hidden items-center pl-2 pr-1 text-zinc-400 sm:inline-flex">
            <Calendar className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          {DATE_RANGE_OPTIONS.map((option) => {
            const active = option.key === dateRange
            return (
              <button
                key={option.key}
                type="button"
                onClick={() => onDateRangeChange(option.key)}
                aria-pressed={active}
                title={option.label}
                className={`cursor-pointer rounded px-2.5 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none ${
                  active
                    ? 'bg-zinc-900 text-white'
                    : 'text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900'
                }`}
              >
                {option.shortLabel}
              </button>
            )
          })}
        </div>

        {/* Refresh button */}
        <Button
          variant="secondary"
          size="sm"
          onClick={onRefresh}
          disabled={isRefreshing}
          aria-label="Refresh dashboard data"
          title={`Last refreshed at ${lastRefreshedLabel}`}
          className="h-[34px]"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 text-zinc-500 ${
              isRefreshing ? 'animate-spin text-emerald-600' : ''
            }`}
            aria-hidden="true"
          />
          <span className="hidden sm:inline">
            {isRefreshing ? 'Refreshing…' : 'Refresh'}
          </span>
          <span className="hidden text-[11px] font-normal text-zinc-400 tabular-nums md:inline">
            · {lastRefreshedLabel}
          </span>
        </Button>

        {/* Primary CTA */}
        <Button
          variant="primary"
          size="sm"
          onClick={() => navigate('/sync-jobs/new')}
          className="h-[34px]"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          <span>Create Sync Job</span>
        </Button>
      </div>
    </div>
  )
}
