import { Link, useParams } from 'react-router-dom'
import { AlertCircle, ArrowLeft, ArrowRight, Clock } from 'lucide-react'
import { useWorkspace } from '../components/layout/WorkspaceContext'
import { Badge } from '../components/ui/Badge'
import type { BadgeTone } from '../components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card'
import { ConnectorIcon } from '../components/ui/ConnectorIcon'
import { MOCK_DASHBOARD_BY_WORKSPACE } from '../data/mockDashboardData'
import type { SyncJobStatus } from '../types/dashboard'

function getStatusTone(status: SyncJobStatus): BadgeTone {
  switch (status) {
    case 'Success':
      return 'emerald'
    case 'Failed':
      return 'rose'
    case 'Running':
      return 'amber'
    case 'Pending':
      return 'zinc'
  }
}

export function SyncJobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const { dashboardData } = useWorkspace()

  const job =
    dashboardData.recentJobs.find((item) => item.id === jobId) ??
    Object.values(MOCK_DASHBOARD_BY_WORKSPACE)
      .flatMap((ws) => ws.recentJobs)
      .find((item) => item.id === jobId)

  if (!job) {
    return (
      <div className="space-y-4">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-600 hover:text-zinc-900"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          Back to Overview
        </Link>
        <Card className="max-w-lg">
          <CardContent className="p-6">
            <h1 className="text-base font-semibold text-zinc-900">
              Sync job not found
            </h1>
            <p className="mt-1 text-xs text-zinc-500">
              No mock sync job matches identifier{' '}
              <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono">
                {jobId}
              </code>
              .
            </p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 border-b border-zinc-200/80 pb-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="font-mono text-xl font-semibold tracking-tight text-zinc-950">
              {job.name}
            </h1>
            <Badge tone={getStatusTone(job.status)} dot>
              {job.status}
            </Badge>
            <Badge tone="zinc">Mock detail preview</Badge>
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            Schedule: {job.schedule} · Last run: {job.lastRunLabel}
          </p>
        </div>

        <Link
          to="/"
          className="inline-flex h-8 items-center gap-1.5 self-start rounded-md border border-zinc-200 bg-white px-3 text-xs font-medium text-zinc-700 hover:bg-zinc-50 hover:text-zinc-900 sm:self-auto"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          Back to Overview
        </Link>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Pipeline Topology &amp; Latest Run</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-3 rounded-md border border-zinc-200/80 bg-zinc-50/70 p-3.5 text-xs">
              <div className="flex items-center gap-2">
                <ConnectorIcon type={job.sourceType} />
                <div>
                  <div className="font-semibold text-zinc-900">
                    {job.sourceName}
                  </div>
                  <div className="text-[11px] text-zinc-500">
                    Source · {job.sourceType}
                  </div>
                </div>
              </div>
              <ArrowRight
                className="h-4 w-4 text-zinc-400"
                aria-hidden="true"
              />
              <div className="flex items-center gap-2">
                <ConnectorIcon type={job.destinationType} />
                <div>
                  <div className="font-semibold text-zinc-900">
                    {job.destinationName}
                  </div>
                  <div className="text-[11px] text-zinc-500">
                    Destination · {job.destinationType}
                  </div>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <div className="rounded-md border border-zinc-200/70 p-3">
                <div className="text-[11px] text-zinc-500">Records</div>
                <div className="mt-1 text-base font-semibold text-zinc-900 tabular-nums">
                  {job.recordsProcessed.toLocaleString()}
                </div>
              </div>
              <div className="rounded-md border border-zinc-200/70 p-3">
                <div className="text-[11px] text-zinc-500">Duration</div>
                <div className="mt-1 text-base font-semibold text-zinc-900 tabular-nums">
                  {job.durationSeconds !== null
                    ? `${job.durationSeconds}s`
                    : '—'}
                </div>
              </div>
              <div className="col-span-2 rounded-md border border-zinc-200/70 p-3 sm:col-span-1">
                <div className="text-[11px] text-zinc-500">Schedule</div>
                <div className="mt-1 text-sm font-medium text-zinc-900">
                  {job.schedule}
                </div>
              </div>
            </div>

            {job.errorMessage && (
              <div className="flex items-start gap-2.5 rounded-md border border-rose-200 bg-rose-50/70 p-3 text-xs text-rose-900">
                <AlertCircle
                  className="mt-0.5 h-4 w-4 shrink-0 text-rose-600"
                  aria-hidden="true"
                />
                <div>
                  <div className="font-semibold">Latest failure reason</div>
                  <p className="mt-0.5 font-mono text-[11px]">
                    {job.errorMessage}
                  </p>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Execution Controls</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5 text-xs text-zinc-600">
            <div className="flex items-center gap-1.5 font-medium text-zinc-900">
              <Clock className="h-3.5 w-3.5 text-amber-600" aria-hidden="true" />
              Coming next
            </div>
            <p className="leading-relaxed">
              Live manual triggers, worker log streaming, checkpoint cursors,
              and retry replay controls will connect once the backend job API is
              integrated.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
