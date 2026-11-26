import { Link } from 'react-router-dom'
import { ArrowLeft, Clock } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useWorkspace } from '../components/layout/WorkspaceContext'
import { Badge } from '../components/ui/Badge'
import { Card, CardContent } from '../components/ui/Card'

interface PlaceholderPageProps {
  title: string
  description: string
  icon: LucideIcon
  plannedCapabilities: string[]
}

export function PlaceholderPage({
  title,
  description,
  icon: Icon,
  plannedCapabilities,
}: PlaceholderPageProps) {
  const { activeWorkspace } = useWorkspace()

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 border-b border-zinc-200/80 pb-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-semibold tracking-tight text-zinc-950 sm:text-2xl">
              {title}
            </h1>
            <Badge tone="amber" dot>
              Coming next
            </Badge>
          </div>
          <p className="mt-1 text-sm text-zinc-500">{description}</p>
        </div>

        <Link
          to="/"
          className="inline-flex h-8 items-center gap-1.5 self-start rounded-md border border-zinc-200 bg-white px-3 text-xs font-medium text-zinc-700 shadow-[0_1px_2px_rgba(15,23,42,0.03)] transition-colors hover:bg-zinc-50 hover:text-zinc-900 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none sm:self-auto"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          Back to Overview
        </Link>
      </div>

      <Card className="max-w-2xl">
        <CardContent className="p-6">
          <div className="flex items-start gap-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-700">
              <Icon className="h-5 w-5" aria-hidden="true" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold text-zinc-900">
                  {title} module is scheduled for the next milestone
                </h2>
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-zinc-600">
                Active workspace:{' '}
                <span className="font-medium text-zinc-900">
                  {activeWorkspace.name}
                </span>{' '}
                ({activeWorkspace.environment}). This controlled placeholder
                reserves the route structure and navigation shell while backend
                services and interactive workflows are built out.
              </p>

              <div className="mt-4 rounded-md border border-zinc-200/80 bg-zinc-50/70 p-3.5">
                <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold tracking-wider text-zinc-500 uppercase">
                  <Clock className="h-3.5 w-3.5" aria-hidden="true" />
                  Planned capabilities
                </div>
                <ul className="space-y-1.5 text-xs text-zinc-700">
                  {plannedCapabilities.map((item) => (
                    <li key={item} className="flex items-center gap-2">
                      <span
                        className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500"
                        aria-hidden="true"
                      />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
