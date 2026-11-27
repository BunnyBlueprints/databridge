import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Compass,
  Info,
  ShieldCheck,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import type {
  MappingStatus,
  MappingValidationSummary,
} from '../../types/mapping.ts'
import { Badge } from '../ui/Badge.tsx'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/Card.tsx'
import {
  formatMappingTimestamp,
  getMappingStatusDisplay,
} from './mappingHelpers.ts'

interface ValidationSummaryPanelProps {
  status: MappingStatus
  validationSummary: MappingValidationSummary | null
  hasUnsavedChanges: boolean
  isNewMapping: boolean
  sourceConnectionId: string
  destinationConnectionId: string
}

export function ValidationSummaryPanel({
  status,
  validationSummary,
  hasUnsavedChanges,
  isNewMapping,
  sourceConnectionId,
  destinationConnectionId,
}: ValidationSummaryPanelProps) {
  const statusDisplay = getMappingStatusDisplay(status)

  return (
    <Card className="dark:border-zinc-800 dark:bg-zinc-900">
      <CardHeader className="px-4 py-3 dark:border-zinc-800">
        <div className="flex w-full flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <ShieldCheck
              className="h-4 w-4 text-emerald-600 dark:text-emerald-400"
              aria-hidden="true"
            />
            <CardTitle className="text-sm dark:text-zinc-100">
              Backend Validation Results
            </CardTitle>
            <Badge tone={statusDisplay.tone} dot>
              {statusDisplay.label}
            </Badge>
          </div>

          {validationSummary && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Badge
                tone={validationSummary.errorCount > 0 ? 'rose' : 'zinc'}
              >
                {validationSummary.errorCount} error
                {validationSummary.errorCount === 1 ? '' : 's'}
              </Badge>
              <Badge
                tone={validationSummary.warningCount > 0 ? 'amber' : 'zinc'}
              >
                {validationSummary.warningCount} warning
                {validationSummary.warningCount === 1 ? '' : 's'}
              </Badge>
              <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                Validated {formatMappingTimestamp(validationSummary.validatedAt)}
              </span>
            </div>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-4 p-4">
        {isNewMapping ? (
          <div className="flex items-start gap-2.5 rounded-md border border-zinc-200 bg-zinc-50/70 px-3 py-2.5 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-300">
            <Info
              className="mt-0.5 h-4 w-4 shrink-0 text-zinc-500"
              aria-hidden="true"
            />
            <span>
              Save this mapping first to persist rules, then run{' '}
              <strong>Validate Mapping</strong> for backend structural, type,
              required-field, and drift verification.
            </span>
          </div>
        ) : hasUnsavedChanges ? (
          <div className="flex items-start gap-2.5 rounded-md border border-amber-200/90 bg-amber-50/80 px-3 py-2.5 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
            <AlertTriangle
              className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400"
              aria-hidden="true"
            />
            <span>
              You have unsaved changes. Save your edits before running backend
              validation against the latest schemas.
            </span>
          </div>
        ) : null}

        {!validationSummary ? (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            No backend validation summary has been recorded for the current
            version yet. Click <strong>Validate Mapping</strong> to evaluate
            type compatibility, required destination fields, and schema drift.
          </p>
        ) : (
          <>
            {validationSummary.schemaDriftDetected && (
              <div
                role="status"
                className="space-y-2 rounded-md border border-amber-200 bg-amber-50/80 px-3.5 py-3 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
              >
                <div className="flex items-start gap-2">
                  <AlertTriangle
                    className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400"
                    aria-hidden="true"
                  />
                  <div className="space-y-1">
                    <div className="font-semibold">Schema Drift Detected</div>
                    <p className="leading-relaxed">
                      One or more schema snapshots changed since this mapping was
                      saved. Rediscover schemas in Data Explorer if needed and
                      review mapping rules.
                    </p>
                    <div className="flex flex-wrap items-center gap-2 pt-1">
                      {validationSummary.sourceDriftDetected && (
                        <Badge tone="amber">Source schema changed</Badge>
                      )}
                      {validationSummary.destinationDriftDetected && (
                        <Badge tone="amber">Destination schema changed</Badge>
                      )}
                      {sourceConnectionId && (
                        <Link
                          to={`/data-explorer?connectionId=${encodeURIComponent(sourceConnectionId)}`}
                          className="inline-flex items-center gap-1 font-medium underline hover:no-underline"
                        >
                          <Compass className="h-3 w-3" aria-hidden="true" />
                          <span>Inspect Source Schema</span>
                        </Link>
                      )}
                      {destinationConnectionId && (
                        <Link
                          to={`/data-explorer?connectionId=${encodeURIComponent(destinationConnectionId)}`}
                          className="inline-flex items-center gap-1 font-medium underline hover:no-underline"
                        >
                          <Compass className="h-3 w-3" aria-hidden="true" />
                          <span>Inspect Destination Schema</span>
                        </Link>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {validationSummary.missingRequiredDestinationFields.length > 0 && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50/70 px-3.5 py-2.5 text-xs text-rose-900 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-200"
              >
                <div className="font-semibold">
                  Missing required destination fields (
                  {validationSummary.missingRequiredDestinationFields.length})
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {validationSummary.missingRequiredDestinationFields.map(
                    (fieldPath) => (
                      <span
                        key={fieldPath}
                        className="rounded border border-rose-200 bg-white px-2 py-0.5 font-mono text-[11px] text-rose-800 dark:border-rose-800 dark:bg-zinc-900 dark:text-rose-300"
                      >
                        {fieldPath}
                      </span>
                    ),
                  )}
                </div>
              </div>
            )}

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-md border border-zinc-200/80 bg-zinc-50/60 p-3 text-xs dark:border-zinc-800 dark:bg-zinc-950/50">
                <div className="font-medium text-zinc-700 dark:text-zinc-300">
                  Unmapped Source Fields (
                  {validationSummary.unmappedSourceFields.length})
                </div>
                {validationSummary.unmappedSourceFields.length === 0 ? (
                  <p className="mt-1 text-[11px] text-zinc-500">
                    All source fields are referenced.
                  </p>
                ) : (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {validationSummary.unmappedSourceFields.map((path) => (
                      <span
                        key={path}
                        className="rounded border border-zinc-200 bg-white px-1.5 py-0.5 font-mono text-[11px] text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
                      >
                        {path}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              <div className="rounded-md border border-zinc-200/80 bg-zinc-50/60 p-3 text-xs dark:border-zinc-800 dark:bg-zinc-950/50">
                <div className="font-medium text-zinc-700 dark:text-zinc-300">
                  Unmapped Destination Fields (
                  {validationSummary.unmappedDestinationFields.length})
                </div>
                {validationSummary.unmappedDestinationFields.length === 0 ? (
                  <p className="mt-1 text-[11px] text-zinc-500">
                    All destination fields are covered.
                  </p>
                ) : (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {validationSummary.unmappedDestinationFields.map((path) => (
                      <span
                        key={path}
                        className="rounded border border-zinc-200 bg-white px-1.5 py-0.5 font-mono text-[11px] text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
                      >
                        {path}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {validationSummary.issues.length === 0 ? (
              <div className="flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50/70 px-3.5 py-2.5 text-xs text-emerald-800 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-200">
                <CheckCircle2
                  className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400"
                  aria-hidden="true"
                />
                <span>
                  All mapping rules, canonical types, and required destination
                  fields passed backend validation with zero errors or warnings.
                </span>
              </div>
            ) : (
              <div className="space-y-2">
                <h3 className="text-xs font-semibold tracking-wider text-zinc-500 uppercase dark:text-zinc-400">
                  Validation Issues ({validationSummary.issues.length})
                </h3>
                <ul className="space-y-2">
                  {validationSummary.issues.map((issue, idx) => {
                    const isError = issue.severity === 'error'
                    return (
                      <li
                        key={`${issue.code}-${idx}`}
                        className={`rounded-md border px-3 py-2.5 text-xs ${
                          isError
                            ? 'border-rose-200/90 bg-rose-50/70 text-rose-950 dark:border-rose-900/60 dark:bg-rose-950/25 dark:text-rose-100'
                            : 'border-amber-200/90 bg-amber-50/70 text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/25 dark:text-amber-100'
                        }`}
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          {isError ? (
                            <AlertCircle
                              className="h-3.5 w-3.5 shrink-0 text-rose-600 dark:text-rose-400"
                              aria-hidden="true"
                            />
                          ) : (
                            <AlertTriangle
                              className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400"
                              aria-hidden="true"
                            />
                          )}
                          <Badge tone={isError ? 'rose' : 'amber'}>
                            {isError ? 'Error' : 'Warning'}
                          </Badge>
                          <span className="font-mono text-[11px] font-semibold">
                            {issue.code}
                          </span>
                          {issue.ordinalPosition !== null &&
                            issue.ordinalPosition !== undefined && (
                              <Badge tone="zinc">
                                Rule #{issue.ordinalPosition + 1}
                              </Badge>
                            )}
                          {issue.ruleId && (
                            <span className="font-mono text-[10px] opacity-75">
                              ({issue.ruleId.slice(0, 8)})
                            </span>
                          )}
                        </div>
                        <p className="mt-1.5 leading-relaxed">{issue.message}</p>
                        {(issue.sourceFieldPath ||
                          issue.destinationFieldPath) && (
                          <div className="mt-1.5 flex flex-wrap items-center gap-3 font-mono text-[11px] opacity-90">
                            {issue.sourceFieldPath && (
                              <span>Source: {issue.sourceFieldPath}</span>
                            )}
                            {issue.destinationFieldPath && (
                              <span>Dest: {issue.destinationFieldPath}</span>
                            )}
                          </div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
