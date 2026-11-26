import { Link } from 'react-router-dom'
import { ArrowUpRight } from 'lucide-react'
import type { ConnectionHealth, ConnectionItem } from '../../types/dashboard'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from '../ui/Card'
import { ConnectorIcon } from '../ui/ConnectorIcon'

interface ConnectionsOverviewProps {
  connections: ConnectionItem[]
}

function getHealthTone(health: ConnectionHealth): BadgeTone {
  switch (health) {
    case 'Healthy':
      return 'emerald'
    case 'Degraded':
      return 'amber'
    case 'Inactive':
      return 'zinc'
  }
}

export function ConnectionsOverview({ connections }: ConnectionsOverviewProps) {
  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <div>
          <CardTitle>Connections</CardTitle>
          <CardDescription>Recently active sources and targets</CardDescription>
        </div>
        <Link
          to="/connections"
          className="inline-flex items-center gap-1 rounded text-xs font-medium text-emerald-700 transition-colors hover:text-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none"
        >
          <span>View all</span>
          <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
        </Link>
      </CardHeader>

      <div className="divide-y divide-zinc-100">
        {connections.map((conn) => (
          <div
            key={conn.id}
            className="flex items-center justify-between gap-3 px-5 py-3 transition-colors hover:bg-zinc-50/70"
          >
            <div className="flex min-w-0 items-center gap-3">
              <ConnectorIcon type={conn.type} />
              <div className="min-w-0">
                <div className="truncate text-xs font-semibold text-zinc-900">
                  {conn.name}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-zinc-500">
                  <span>{conn.type}</span>
                  <span aria-hidden="true">·</span>
                  <span className="truncate font-mono text-[10px] text-zinc-400">
                    {conn.hostHint}
                  </span>
                </div>
              </div>
            </div>

            <div className="flex shrink-0 flex-col items-end gap-1">
              <Badge tone={getHealthTone(conn.health)} dot>
                {conn.health}
              </Badge>
              <span className="text-[11px] text-zinc-400 tabular-nums">
                Synced {conn.lastSuccessfulSync}
              </span>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}
