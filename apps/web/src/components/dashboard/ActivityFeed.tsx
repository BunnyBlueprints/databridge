import { Link } from 'react-router-dom'
import {
  AlertCircle,
  ArrowUpRight,
  Cable,
  CalendarClock,
  CheckCircle2,
  Workflow,
} from 'lucide-react'
import type { ActivityEvent, ActivityStatus, ActivityType } from '../../types/dashboard'
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from '../ui/Card'

interface ActivityFeedProps {
  events: ActivityEvent[]
}

function getActivityIcon(type: ActivityType) {
  switch (type) {
    case 'sync_completed':
      return CheckCircle2
    case 'sync_failed':
      return AlertCircle
    case 'connection_created':
      return Cable
    case 'mapping_updated':
      return Workflow
    case 'sync_scheduled':
      return CalendarClock
  }
}

const STATUS_STYLE: Record<
  ActivityStatus,
  { iconBg: string; iconFg: string; border: string }
> = {
  success: {
    iconBg: 'bg-emerald-50',
    iconFg: 'text-emerald-600',
    border: 'border-emerald-200/70',
  },
  error: {
    iconBg: 'bg-rose-50',
    iconFg: 'text-rose-600',
    border: 'border-rose-200/70',
  },
  warning: {
    iconBg: 'bg-amber-50',
    iconFg: 'text-amber-600',
    border: 'border-amber-200/70',
  },
  info: {
    iconBg: 'bg-zinc-100',
    iconFg: 'text-zinc-600',
    border: 'border-zinc-200/80',
  },
}

export function ActivityFeed({ events }: ActivityFeedProps) {
  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <div>
          <CardTitle>Platform Activity</CardTitle>
          <CardDescription>Recent sync, schema, and connector events</CardDescription>
        </div>
        <Link
          to="/activity-logs"
          className="inline-flex items-center gap-1 rounded text-xs font-medium text-emerald-700 transition-colors hover:text-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none"
        >
          <span>Logs</span>
          <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
        </Link>
      </CardHeader>

      <div className="flex-1 px-5 py-4">
        <ol className="relative space-y-4 before:absolute before:top-2 before:bottom-2 before:left-[13px] before:w-px before:bg-zinc-200/80">
          {events.map((event) => {
            const Icon = getActivityIcon(event.type)
            const style = STATUS_STYLE[event.status]
            return (
              <li key={event.id} className="relative flex gap-3 pl-0.5">
                <span
                  className={`relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border ${style.iconBg} ${style.iconFg} ${style.border}`}
                >
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1 pt-0.5">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate font-mono text-[11px] font-medium text-zinc-900">
                      {event.actorOrTarget}
                    </span>
                    <span className="shrink-0 text-[11px] text-zinc-400 tabular-nums">
                      {event.timestamp}
                    </span>
                  </div>
                  <p className="mt-0.5 text-xs leading-relaxed text-zinc-600">
                    {event.description}
                  </p>
                </div>
              </li>
            )
          })}
        </ol>
      </div>
    </Card>
  )
}
