import { Link } from 'react-router-dom'
import {
  Activity,
  ArrowRight,
  Cable,
  PlaySquare,
  Workflow,
} from 'lucide-react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '../ui/Card'

const QUICK_ACTIONS = [
  {
    title: 'Create Sync Job',
    description: 'Configure a source-to-destination pipeline',
    to: '/sync-jobs/new',
    icon: PlaySquare,
  },
  {
    title: 'Add Connection',
    description: 'Register a database, API, or file connector',
    to: '/connections',
    icon: Cable,
  },
  {
    title: 'Open Mapping Studio',
    description: 'Inspect field mappings and transforms',
    to: '/mapping-studio',
    icon: Workflow,
  },
  {
    title: 'View Activity Logs',
    description: 'Audit historical runs and platform events',
    to: '/activity-logs',
    icon: Activity,
  },
] as const

export function QuickActions() {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Quick Actions</CardTitle>
          <CardDescription>Common engineering workflows</CardDescription>
        </div>
      </CardHeader>

      <CardContent className="grid grid-cols-1 gap-2.5 p-4 sm:grid-cols-2 lg:grid-cols-4">
        {QUICK_ACTIONS.map((action) => {
          const Icon = action.icon
          return (
            <Link
              key={action.to}
              to={action.to}
              className="group flex items-center justify-between gap-3 rounded-md border border-zinc-200/80 bg-zinc-50/50 p-3 transition-colors hover:border-emerald-300/80 hover:bg-emerald-50/40 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none"
            >
              <div className="flex min-w-0 items-center gap-3">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-zinc-200/80 bg-white text-zinc-600 transition-colors group-hover:border-emerald-200 group-hover:text-emerald-700">
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </span>
                <div className="min-w-0">
                  <div className="truncate text-xs font-semibold text-zinc-900">
                    {action.title}
                  </div>
                  <div className="truncate text-[11px] text-zinc-500">
                    {action.description}
                  </div>
                </div>
              </div>
              <ArrowRight
                className="h-3.5 w-3.5 shrink-0 text-zinc-400 transition-transform group-hover:translate-x-0.5 group-hover:text-emerald-700"
                aria-hidden="true"
              />
            </Link>
          )
        })}
      </CardContent>
    </Card>
  )
}
