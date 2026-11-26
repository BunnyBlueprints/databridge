import { useEffect, useMemo, useRef, useState } from 'react'
import { DATE_RANGE_OPTIONS } from '../../data/mockDashboardData'
import type { DateRangeKey, SyncActivityPoint } from '../../types/dashboard'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '../ui/Card'

interface SyncActivityChartProps {
  seriesByRange: Record<DateRangeKey, SyncActivityPoint[]>
  range: DateRangeKey
  onRangeChange: (range: DateRangeKey) => void
}

function formatCompactCount(num: number): string {
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`
  if (num >= 1_000) return `${(num / 1_000).toFixed(0)}K`
  return num.toString()
}

export function SyncActivityChart({
  seriesByRange,
  range,
  onRangeChange,
}: SyncActivityChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [containerWidth, setContainerWidth] = useState(640)
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null)
  const [showRecordsLine, setShowRecordsLine] = useState(true)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const updateWidth = () => {
      const next = Math.max(300, Math.floor(el.clientWidth))
      setContainerWidth(next)
    }

    updateWidth()
    const observer = new ResizeObserver(updateWidth)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const data = useMemo(() => seriesByRange[range], [seriesByRange, range])

  const totals = useMemo(() => {
    return data.reduce(
      (acc, point) => ({
        successful: acc.successful + point.successfulSyncs,
        failed: acc.failed + point.failedSyncs,
        records: acc.records + point.recordsProcessed,
      }),
      { successful: 0, failed: 0, records: 0 },
    )
  }, [data])

  const chartHeight = 236
  const padding = {
    top: 16,
    right: showRecordsLine ? 52 : 16,
    bottom: 28,
    left: 44,
  }
  const innerWidth = Math.max(120, containerWidth - padding.left - padding.right)
  const innerHeight = chartHeight - padding.top - padding.bottom

  const maxSyncs = useMemo(() => {
    const peak = Math.max(
      10,
      ...data.map((d) => d.successfulSyncs + d.failedSyncs),
    )
    return Math.ceil(peak * 1.15)
  }, [data])

  const maxRecords = useMemo(() => {
    const peak = Math.max(1000, ...data.map((d) => d.recordsProcessed))
    return Math.ceil(peak * 1.15)
  }, [data])

  const yTicks = [0, 0.25, 0.5, 0.75, 1]
  const bucketWidth = data.length > 0 ? innerWidth / data.length : innerWidth
  const barWidth = Math.min(28, Math.max(10, bucketWidth * 0.42))

  const recordsLinePath = useMemo(() => {
    if (!showRecordsLine || data.length === 0) return ''
    return data
      .map((pt, idx) => {
        const x = padding.left + bucketWidth * idx + bucketWidth / 2
        const y =
          padding.top +
          innerHeight -
          (pt.recordsProcessed / maxRecords) * innerHeight
        return `${idx === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
      })
      .join(' ')
  }, [
    showRecordsLine,
    data,
    padding.left,
    padding.top,
    bucketWidth,
    innerHeight,
    maxRecords,
  ])

  const hoveredPoint = hoveredIdx !== null ? data[hoveredIdx] : null
  const hoveredCenterX =
    hoveredIdx !== null
      ? padding.left + bucketWidth * hoveredIdx + bucketWidth / 2
      : 0

  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <div>
          <CardTitle>Sync Activity</CardTitle>
          <CardDescription>
            Successful vs. failed runs and record throughput
          </CardDescription>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="hidden items-center gap-4 text-xs text-zinc-500 sm:flex">
            <span>
              <strong className="font-semibold text-zinc-900 tabular-nums">
                {totals.successful.toLocaleString()}
              </strong>{' '}
              succeeded
            </span>
            <span>
              <strong className="font-semibold text-rose-700 tabular-nums">
                {totals.failed.toLocaleString()}
              </strong>{' '}
              failed
            </span>
            <span>
              <strong className="font-semibold text-zinc-900 tabular-nums">
                {formatCompactCount(totals.records)}
              </strong>{' '}
              records
            </span>
          </div>

          <div
            className="inline-flex rounded-md border border-zinc-200 bg-zinc-50 p-0.5"
            role="group"
            aria-label="Sync activity time range"
          >
            {DATE_RANGE_OPTIONS.map((opt) => {
              const active = opt.key === range
              return (
                <button
                  key={opt.key}
                  type="button"
                  onClick={() => {
                    setHoveredIdx(null)
                    onRangeChange(opt.key)
                  }}
                  aria-pressed={active}
                  className={`cursor-pointer rounded px-2 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none ${
                    active
                      ? 'bg-white text-zinc-900 shadow-[0_1px_2px_rgba(15,23,42,0.06)]'
                      : 'text-zinc-500 hover:text-zinc-900'
                  }`}
                >
                  {opt.shortLabel}
                </button>
              )
            })}
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex flex-1 flex-col justify-between pt-4">
        {/* Legend */}
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="flex flex-wrap items-center gap-4">
            <span className="inline-flex items-center gap-1.5 text-zinc-600">
              <span
                className="h-2.5 w-2.5 rounded-[2px] bg-emerald-600"
                aria-hidden="true"
              />
              Successful syncs
            </span>
            <span className="inline-flex items-center gap-1.5 text-zinc-600">
              <span
                className="h-2.5 w-2.5 rounded-[2px] bg-rose-500"
                aria-hidden="true"
              />
              Failed syncs
            </span>
          </div>

          <button
            type="button"
            onClick={() => setShowRecordsLine((prev) => !prev)}
            aria-pressed={showRecordsLine}
            className={`inline-flex cursor-pointer items-center gap-1.5 rounded border px-2 py-0.5 text-xs transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none ${
              showRecordsLine
                ? 'border-zinc-200 bg-zinc-50 text-zinc-700'
                : 'border-transparent text-zinc-400 hover:text-zinc-600'
            }`}
          >
            <span
              className="h-0.5 w-3 border-t-2 border-dashed border-slate-500"
              aria-hidden="true"
            />
            Records processed
          </button>
        </div>

        {/* Chart area */}
        <div
          ref={containerRef}
          className="relative w-full select-none"
          onMouseLeave={() => setHoveredIdx(null)}
        >
          <svg
            width={containerWidth}
            height={chartHeight}
            viewBox={`0 0 ${containerWidth} ${chartHeight}`}
            className="block w-full overflow-visible"
            role="img"
            aria-label={`Sync activity chart for ${range}`}
          >
            {/* Horizontal grid lines & Y axis labels */}
            {yTicks.map((ratio) => {
              const y = padding.top + innerHeight - ratio * innerHeight
              const syncVal = Math.round(ratio * maxSyncs)
              const recVal = Math.round(ratio * maxRecords)
              return (
                <g key={ratio}>
                  <line
                    x1={padding.left}
                    y1={y}
                    x2={padding.left + innerWidth}
                    y2={y}
                    stroke={ratio === 0 ? '#d4d4d8' : '#f1f5f9'}
                    strokeDasharray={ratio === 0 ? undefined : '3 3'}
                    strokeWidth="1"
                  />
                  <text
                    x={padding.left - 8}
                    y={y + 3.5}
                    textAnchor="end"
                    className="fill-zinc-400 text-[10px] tabular-nums"
                  >
                    {syncVal}
                  </text>
                  {showRecordsLine && (
                    <text
                      x={padding.left + innerWidth + 8}
                      y={y + 3.5}
                      textAnchor="start"
                      className="fill-slate-400 text-[10px] tabular-nums"
                    >
                      {formatCompactCount(recVal)}
                    </text>
                  )}
                </g>
              )
            })}

            {/* Stacked bars + hover hit areas */}
            {data.map((point, idx) => {
              const centerX = padding.left + bucketWidth * idx + bucketWidth / 2
              const barX = centerX - barWidth / 2

              const successHeight =
                (point.successfulSyncs / maxSyncs) * innerHeight
              const failedHeight =
                point.failedSyncs > 0
                  ? Math.max(2, (point.failedSyncs / maxSyncs) * innerHeight)
                  : 0

              const successY = padding.top + innerHeight - successHeight
              const failedY = successY - failedHeight
              const isHovered = hoveredIdx === idx

              return (
                <g key={point.label}>
                  {isHovered && (
                    <rect
                      x={padding.left + bucketWidth * idx + 2}
                      y={padding.top}
                      width={Math.max(0, bucketWidth - 4)}
                      height={innerHeight}
                      rx="4"
                      className="fill-zinc-100/80"
                    />
                  )}

                  {/* Successful bar */}
                  <rect
                    x={barX}
                    y={successY}
                    width={barWidth}
                    height={Math.max(0, successHeight)}
                    rx={point.failedSyncs === 0 ? 3 : 0}
                    className={`transition-opacity ${
                      isHovered ? 'fill-emerald-700' : 'fill-emerald-600'
                    }`}
                  />

                  {/* Failed bar stacked cleanly on top */}
                  {point.failedSyncs > 0 && (
                    <rect
                      x={barX}
                      y={failedY}
                      width={barWidth}
                      height={failedHeight}
                      rx="2"
                      className={isHovered ? 'fill-rose-600' : 'fill-rose-500'}
                    />
                  )}

                  {/* X-axis label */}
                  <text
                    x={centerX}
                    y={padding.top + innerHeight + 18}
                    textAnchor="middle"
                    className={`text-[11px] tabular-nums ${
                      isHovered
                        ? 'fill-zinc-900 font-medium'
                        : 'fill-zinc-500'
                    }`}
                  >
                    {point.label}
                  </text>

                  {/* Interactive column hitbox */}
                  <rect
                    x={padding.left + bucketWidth * idx}
                    y={padding.top}
                    width={bucketWidth}
                    height={innerHeight + 24}
                    fill="transparent"
                    tabIndex={0}
                    role="button"
                    aria-label={`${point.fullLabel}: ${point.successfulSyncs} successful, ${point.failedSyncs} failed, ${point.recordsProcessed.toLocaleString()} records`}
                    onMouseEnter={() => setHoveredIdx(idx)}
                    onFocus={() => setHoveredIdx(idx)}
                    onBlur={() => setHoveredIdx(null)}
                    className="cursor-pointer outline-none"
                  />
                </g>
              )
            })}

            {/* Records processed overlay line */}
            {showRecordsLine && recordsLinePath && (
              <g className="pointer-events-none">
                <path
                  d={recordsLinePath}
                  fill="none"
                  stroke="#64748b"
                  strokeWidth="1.75"
                  strokeDasharray="4 3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                {data.map((pt, idx) => {
                  const cx = padding.left + bucketWidth * idx + bucketWidth / 2
                  const cy =
                    padding.top +
                    innerHeight -
                    (pt.recordsProcessed / maxRecords) * innerHeight
                  const isHovered = hoveredIdx === idx
                  return (
                    <circle
                      key={pt.label}
                      cx={cx}
                      cy={cy}
                      r={isHovered ? 4 : 2.5}
                      className={
                        isHovered
                          ? 'fill-slate-800 stroke-white stroke-[1.5]'
                          : 'fill-slate-600 stroke-white stroke-1'
                      }
                    />
                  )
                })}
              </g>
            )}
          </svg>

          {/* Interactive Tooltip */}
          {hoveredPoint && hoveredIdx !== null && (
            <div
              role="status"
              style={{
                left: `${Math.min(
                  Math.max(hoveredCenterX, 92),
                  containerWidth - 92,
                )}px`,
              }}
              className="pointer-events-none absolute top-1 z-20 w-44 -translate-x-1/2 rounded-md border border-zinc-200 bg-white/95 p-2.5 text-xs shadow-md"
            >
              <div className="mb-1.5 border-b border-zinc-100 pb-1 font-medium text-zinc-900">
                {hoveredPoint.fullLabel}
              </div>
              <div className="space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1.5 text-zinc-600">
                    <span className="h-2 w-2 rounded-[2px] bg-emerald-600" />
                    Success
                  </span>
                  <span className="font-semibold text-zinc-900 tabular-nums">
                    {hoveredPoint.successfulSyncs.toLocaleString()}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1.5 text-zinc-600">
                    <span className="h-2 w-2 rounded-[2px] bg-rose-500" />
                    Failed
                  </span>
                  <span className="font-semibold text-rose-700 tabular-nums">
                    {hoveredPoint.failedSyncs.toLocaleString()}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1.5 text-zinc-600">
                    <span className="h-0.5 w-2 bg-slate-500" />
                    Records
                  </span>
                  <span className="font-semibold text-zinc-900 tabular-nums">
                    {hoveredPoint.recordsProcessed.toLocaleString()}
                  </span>
                </div>
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
