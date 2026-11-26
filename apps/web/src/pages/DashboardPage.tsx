import { useEffect, useRef, useState } from 'react'
import { ActivityFeed } from '../components/dashboard/ActivityFeed'
import { ConnectionsOverview } from '../components/dashboard/ConnectionsOverview'
import { DashboardHeader } from '../components/dashboard/DashboardHeader'
import { MetricCard } from '../components/dashboard/MetricCard'
import { QuickActions } from '../components/dashboard/QuickActions'
import { RecentSyncJobs } from '../components/dashboard/RecentSyncJobs'
import { SyncActivityChart } from '../components/dashboard/SyncActivityChart'
import { useWorkspace } from '../components/layout/WorkspaceContext'
import type { DateRangeKey } from '../types/dashboard'

function formatTimeLabel(date: Date): string {
  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function DashboardPage() {
  const { activeWorkspace, dashboardData } = useWorkspace()
  const [dateRange, setDateRange] = useState<DateRangeKey>('7d')
  const [chartRange, setChartRange] = useState<DateRangeKey>('7d')
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [lastRefreshedLabel, setLastRefreshedLabel] = useState(() =>
    formatTimeLabel(new Date()),
  )
  const refreshTimerRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current)
      }
    }
  }, [])

  const handleHeaderDateRangeChange = (nextRange: DateRangeKey) => {
    setDateRange(nextRange)
    setChartRange(nextRange)
  }

  const handleRefresh = () => {
    if (isRefreshing) return
    setIsRefreshing(true)
    refreshTimerRef.current = window.setTimeout(() => {
      setLastRefreshedLabel(formatTimeLabel(new Date()))
      setIsRefreshing(false)
      refreshTimerRef.current = null
    }, 350)
  }

  const kpis = dashboardData.kpisByRange[dateRange]

  return (
    <div className="space-y-5">
      <DashboardHeader
        dateRange={dateRange}
        onDateRangeChange={handleHeaderDateRangeChange}
        onRefresh={handleRefresh}
        isRefreshing={isRefreshing}
        lastRefreshedLabel={lastRefreshedLabel}
        workspaceName={activeWorkspace.name}
      />

      {/* Summary KPI cards */}
      <section aria-label="Key integration metrics">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {kpis.map((metric) => (
            <MetricCard key={metric.id} metric={metric} />
          ))}
        </div>
      </section>

      {/* Sync activity chart + Connections overview */}
      <section
        aria-label="Pipeline activity and connections"
        className="grid grid-cols-1 gap-4 xl:grid-cols-12"
      >
        <div className="xl:col-span-8">
          <SyncActivityChart
            seriesByRange={dashboardData.activitySeriesByRange}
            range={chartRange}
            onRangeChange={setChartRange}
          />
        </div>
        <div className="xl:col-span-4">
          <ConnectionsOverview connections={dashboardData.connections} />
        </div>
      </section>

      {/* Recent sync jobs table + Activity feed */}
      <section
        aria-label="Recent sync jobs and platform activity"
        className="grid grid-cols-1 gap-4 xl:grid-cols-12"
      >
        <div className="min-w-0 xl:col-span-8">
          <RecentSyncJobs jobs={dashboardData.recentJobs} />
        </div>
        <div className="xl:col-span-4">
          <ActivityFeed events={dashboardData.activityFeed} />
        </div>
      </section>

      {/* Quick actions */}
      <section aria-label="Quick actions">
        <QuickActions />
      </section>
    </div>
  )
}
