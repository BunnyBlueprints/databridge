import { useId } from 'react'
import {
  Activity,
  Cable,
  CheckCircle2,
  DatabaseZap,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import type { KpiMetricData } from '../../types/dashboard'
import { Card } from '../ui/Card'

interface MetricCardProps {
  metric: KpiMetricData
}

function Sparkline({
  values,
  tone,
}: {
  values: number[]
  tone: 'emerald' | 'rose' | 'slate'
}) {
  const gradientId = useId()
  if (values.length < 2) return null

  const width = 96
  const height = 32
  const pad = 2
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min

  const coords = values.map((val, idx) => {
    const x = pad + (idx / (values.length - 1)) * (width - pad * 2)
    const normalized = range === 0 ? 0.5 : (val - min) / range
    const y = height - pad - normalized * (height - pad * 2)
    return { x, y }
  })

  const linePath = coords
    .map((pt, idx) => `${idx === 0 ? 'M' : 'L'} ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`)
    .join(' ')

  const areaPath = `${linePath} L ${coords[coords.length - 1].x.toFixed(1)} ${height} L ${coords[0].x.toFixed(1)} ${height} Z`

  const strokeColor =
    tone === 'rose' ? '#e11d48' : tone === 'slate' ? '#64748b' : '#059669'

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="shrink-0 overflow-visible"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={strokeColor} stopOpacity="0.16" />
          <stop offset="100%" stopColor={strokeColor} stopOpacity="0.0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradientId})`} />
      <path
        d={linePath}
        fill="none"
        stroke={strokeColor}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle
        cx={coords[coords.length - 1].x}
        cy={coords[coords.length - 1].y}
        r="2"
        fill={strokeColor}
      />
    </svg>
  )
}

function MetricIcon({ id }: { id: KpiMetricData['id'] }) {
  const iconClass = 'h-4 w-4 text-zinc-500'
  switch (id) {
    case 'connections':
      return <Cable className={iconClass} aria-hidden="true" />
    case 'sync_jobs':
      return <Activity className={iconClass} aria-hidden="true" />
    case 'records_processed':
      return <DatabaseZap className={iconClass} aria-hidden="true" />
    case 'success_rate':
      return <CheckCircle2 className={iconClass} aria-hidden="true" />
  }
}

export function MetricCard({ metric }: MetricCardProps) {
  const sparkTone: 'emerald' | 'rose' | 'slate' = metric.trend
    ? metric.trend.isPositive
      ? 'emerald'
      : 'rose'
    : metric.indicator?.tone === 'neutral'
      ? 'slate'
      : 'emerald'

  return (
    <Card className="flex h-full flex-col justify-between p-4 transition-colors hover:border-zinc-300/90">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-zinc-500">
          {metric.label}
        </span>
        <span className="flex h-7 w-7 items-center justify-center rounded-md border border-zinc-100 bg-zinc-50">
          <MetricIcon id={metric.id} />
        </span>
      </div>

      <div className="mt-3 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="text-2xl font-semibold tracking-tight text-zinc-950 tabular-nums">
            {metric.value}
          </div>
        </div>
        <Sparkline values={metric.sparkline} tone={sparkTone} />
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-zinc-100 pt-2.5 text-xs">
        <span className="truncate text-zinc-500">{metric.secondaryText}</span>

        {metric.trend && (
          <span
            className={`inline-flex items-center gap-1 font-medium tabular-nums ${
              metric.trend.isPositive ? 'text-emerald-700' : 'text-rose-700'
            }`}
          >
            {metric.trend.direction === 'up' ? (
              <TrendingUp className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <TrendingDown className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {metric.trend.value}
          </span>
        )}

        {metric.indicator && (
          <span className="inline-flex items-center gap-1.5 font-medium text-zinc-700">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                metric.indicator.tone === 'emerald'
                  ? 'bg-emerald-500'
                  : metric.indicator.tone === 'amber'
                    ? 'bg-amber-500'
                    : 'bg-zinc-400'
              }`}
              aria-hidden="true"
            />
            {metric.indicator.label}
          </span>
        )}
      </div>
    </Card>
  )
}
