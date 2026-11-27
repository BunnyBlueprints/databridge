import { useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Info,
} from 'lucide-react'
import type {
  DiscoveryStatus,
  SchemaWarning,
  SnapshotDiscoverySource,
} from '../../types/schema.ts'
import { Badge } from '../ui/Badge.tsx'
import { Card, CardContent } from '../ui/Card.tsx'
import {
  formatDiscoveryTimestamp,
  isInferredDiscoverySource,
} from './discoveryForm.ts'

interface SchemaSummaryBannerProps {
  discoveryStatus: DiscoveryStatus
  discoverySource: SnapshotDiscoverySource
  discoveredAt: string
  schemaFingerprint: string
  objectCount: number
  warnings: SchemaWarning[]
}

const SOURCE_LABELS: Record<SnapshotDiscoverySource, string> = {
  catalog: 'Database Catalog',
  openapi: 'OpenAPI Specification',
  inferred_sample: 'Sample Inference',
  manual: 'Manual Declaration',
  mixed: 'Mixed Sources',
}

export function SchemaSummaryBanner({
  discoveryStatus,
  discoverySource,
  discoveredAt,
  schemaFingerprint,
  objectCount,
  warnings,
}: SchemaSummaryBannerProps) {
  const [warningsExpanded, setWarningsExpanded] = useState(warnings.length > 0)
  const isPartial = discoveryStatus === 'partial'
  const isInferred = isInferredDiscoverySource(discoverySource)

  return (
    <Card className="dark:border-zinc-800 dark:bg-zinc-900">
      <CardContent className="space-y-3 px-5 py-3.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2.5">
            {isPartial ? (
              <Badge tone="amber" dot>
                Partial Discovery
              </Badge>
            ) : (
              <Badge tone="emerald" dot>
                Completed
              </Badge>
            )}
            <Badge tone="zinc">{SOURCE_LABELS[discoverySource]}</Badge>
            <span className="text-xs text-zinc-600 dark:text-zinc-300">
              <strong className="font-semibold text-zinc-900 tabular-nums dark:text-zinc-100">
                {objectCount}
              </strong>{' '}
              {objectCount === 1 ? 'object' : 'objects'}
            </span>
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              · {warnings.length}{' '}
              {warnings.length === 1 ? 'warning' : 'warnings'}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-3 text-[11px] text-zinc-500 dark:text-zinc-400">
            <span className="tabular-nums">
              Discovered: {formatDiscoveryTimestamp(discoveredAt)}
            </span>
            <span
              className="rounded border border-zinc-200 bg-zinc-50 px-2 py-0.5 font-mono text-zinc-700 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300"
              title={`Schema SHA-256 Fingerprint: ${schemaFingerprint}`}
            >
              fp:{schemaFingerprint.slice(0, 12)}
            </span>
          </div>
        </div>

        {isPartial && (
          <div
            role="status"
            className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100"
          >
            <AlertTriangle
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400"
              aria-hidden="true"
            />
            <span>
              Discovery finished partially: some objects or fields hit limits,
              permission restrictions, or truncation warnings. Review warnings
              before treating this snapshot as exhaustive.
            </span>
          </div>
        )}

        {isInferred && (
          <div className="flex items-start gap-2 rounded-md border border-zinc-200/90 bg-zinc-50/70 px-3 py-2 text-xs text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-300">
            <Info
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-500 dark:text-zinc-400"
              aria-hidden="true"
            />
            <span>
              Inferred schema types and nullability are structural estimates
              from sampled records and may not reflect unseen rows or variants.
            </span>
          </div>
        )}

        {!isPartial && !isInferred && warnings.length === 0 && (
          <div className="flex items-center gap-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
            <CheckCircle2
              className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400"
              aria-hidden="true"
            />
            <span>
              Snapshot persisted cleanly with zero discovery warnings.
            </span>
          </div>
        )}

        {warnings.length > 0 && (
          <div className="rounded-md border border-zinc-200/80 bg-zinc-50/50 dark:border-zinc-800 dark:bg-zinc-950/40">
            <button
              type="button"
              onClick={() => setWarningsExpanded((v) => !v)}
              aria-expanded={warningsExpanded}
              className="flex w-full cursor-pointer items-center justify-between px-3 py-2 text-left text-xs font-medium text-zinc-700 hover:text-zinc-900 dark:text-zinc-300 dark:hover:text-zinc-100"
            >
              <span>Discovery Warnings ({warnings.length})</span>
              {warningsExpanded ? (
                <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>

            {warningsExpanded && (
              <ul className="divide-y divide-zinc-200/70 border-t border-zinc-200/80 px-3 py-1 text-xs dark:divide-zinc-800 dark:border-zinc-800">
                {warnings.map((w, index) => (
                  <li
                    key={`${w.code}-${w.objectKey ?? ''}-${w.fieldPath ?? ''}-${index}`}
                    className="flex flex-wrap items-baseline gap-2 py-1.5 text-zinc-700 dark:text-zinc-300"
                  >
                    <span className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-amber-800 dark:bg-amber-950/60 dark:text-amber-300">
                      {w.code}
                    </span>
                    {(w.objectKey || w.fieldPath) && (
                      <span className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
                        {[w.objectKey, w.fieldPath].filter(Boolean).join('.')}
                      </span>
                    )}
                    <span>{w.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
