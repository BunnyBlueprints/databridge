import { AlertTriangle, KeyRound, Loader2, Search, X } from 'lucide-react'
import type {
  CanonicalFieldType,
  SchemaField,
  SchemaObjectSummary,
} from '../../types/schema.ts'
import { Badge, type BadgeTone } from '../ui/Badge.tsx'
import { Button } from '../ui/Button.tsx'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/Card.tsx'
import { formatObjectConfidence } from './discoveryForm.ts'

interface FieldInspectorProps {
  selectedObject: SchemaObjectSummary | null
  fields: SchemaField[]
  total: number
  limit: number
  offset: number
  fieldQuery: string
  isLoading: boolean
  error: string | null
  onFieldQueryChange: (value: string) => void
  onPageChange: (nextOffset: number) => void
  onRetry: () => void
}

function canonicalTypeTone(type: CanonicalFieldType): BadgeTone {
  if (type === 'mixed' || type === 'unknown') return 'amber'
  if (type === 'integer' || type === 'number' || type === 'boolean') {
    return 'emerald'
  }
  if (type === 'object' || type === 'array' || type === 'json') return 'slate'
  return 'zinc'
}

export function FieldInspector({
  selectedObject,
  fields,
  total,
  limit,
  offset,
  fieldQuery,
  isLoading,
  error,
  onFieldQueryChange,
  onPageChange,
  onRetry,
}: FieldInspectorProps) {
  if (!selectedObject) {
    return (
      <Card className="dark:border-zinc-800 dark:bg-zinc-900">
        <CardContent className="flex flex-col items-center justify-center px-6 py-16 text-center">
          <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            Select an object to inspect fields
          </p>
          <p className="mt-1 max-w-sm text-xs text-zinc-500 dark:text-zinc-400">
            Choose a discovered table, view, collection, endpoint, or file from
            the object browser to view columns, native and canonical types, and
            constraints.
          </p>
        </CardContent>
      </Card>
    )
  }

  const confidence = formatObjectConfidence(selectedObject.canonicalMetadata)
  const pkFields = Array.isArray(
    selectedObject.canonicalMetadata.primaryKeyFields,
  )
    ? selectedObject.canonicalMetadata.primaryKeyFields.filter(
        (v): v is string => typeof v === 'string',
      )
    : []
  const sampleSize =
    typeof selectedObject.nativeMetadata.sampleSize === 'number'
      ? selectedObject.nativeMetadata.sampleSize
      : null

  const rangeStart = total === 0 ? 0 : offset + 1
  const rangeEnd = Math.min(offset + fields.length, total)
  const canPrev = offset > 0
  const canNext = offset + limit < total

  return (
    <Card className="flex flex-col dark:border-zinc-800 dark:bg-zinc-900">
      <CardHeader className="px-5 py-3.5 dark:border-zinc-800">
        <div className="flex w-full flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="font-mono text-sm dark:text-zinc-100">
                {selectedObject.objectKey}
              </CardTitle>
              <Badge tone="zinc">{selectedObject.objectType}</Badge>
              <Badge tone="slate">{selectedObject.discoverySource}</Badge>
              {confidence && (
                <Badge tone="zinc">Confidence {confidence}</Badge>
              )}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-zinc-500 dark:text-zinc-400">
              <span className="tabular-nums">{total} fields</span>
              {pkFields.length > 0 && (
                <span className="inline-flex items-center gap-1 font-mono">
                  <KeyRound className="h-3 w-3" aria-hidden="true" />
                  PK: {pkFields.join(', ')}
                </span>
              )}
              {sampleSize !== null && (
                <span className="tabular-nums">
                  Sample size: {sampleSize}
                </span>
              )}
            </div>
          </div>

          <div className="relative w-full sm:w-56">
            <Search
              className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400"
              aria-hidden="true"
            />
            <input
              type="text"
              value={fieldQuery}
              onChange={(e) => onFieldQueryChange(e.target.value)}
              placeholder="Filter fields…"
              aria-label="Filter object fields"
              className="h-8 w-full rounded-md border border-zinc-200 bg-white pr-7 pl-8 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
            />
            {fieldQuery !== '' && (
              <button
                type="button"
                onClick={() => onFieldQueryChange('')}
                aria-label="Clear field filter"
                className="absolute top-1/2 right-1.5 -translate-y-1/2 cursor-pointer rounded p-0.5 text-zinc-400 hover:text-zinc-700"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      </CardHeader>

      <CardContent className="p-0">
        {isLoading ? (
          <div className="flex items-center justify-center py-12 text-xs text-zinc-500">
            <Loader2
              className="mr-2 h-4 w-4 animate-spin text-emerald-600"
              aria-hidden="true"
            />
            <span>Loading field metadata…</span>
          </div>
        ) : error ? (
          <div role="alert" className="px-5 py-10 text-center">
            <p className="text-xs text-rose-600">{error}</p>
            <Button
              variant="secondary"
              size="sm"
              onClick={onRetry}
              className="mt-3"
            >
              Retry
            </Button>
          </div>
        ) : fields.length === 0 ? (
          <div className="px-5 py-10 text-center text-xs text-zinc-500 dark:text-zinc-400">
            {fieldQuery.trim()
              ? 'No fields match your filter.'
              : 'This object currently has no discovered fields.'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-zinc-200/80 bg-zinc-50/80 text-[11px] font-semibold text-zinc-500 uppercase dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-400">
                  <th className="py-2.5 pr-2 pl-4">#</th>
                  <th className="px-3 py-2.5">Field &amp; Path</th>
                  <th className="px-3 py-2.5">Canonical</th>
                  <th className="px-3 py-2.5">Native Type</th>
                  <th className="px-3 py-2.5">Null / Required</th>
                  <th className="px-3 py-2.5">Keys &amp; Constraints</th>
                  <th className="py-2.5 pr-4 pl-3">Defaults &amp; Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {fields.map((field) => {
                  const depth =
                    typeof field.metadata.depth === 'number'
                      ? Math.min(6, Math.max(0, field.metadata.depth))
                      : 0
                  const fk = field.metadata.foreignKey
                  const inferredTypes = Array.isArray(
                    field.metadata.inferredTypes,
                  )
                    ? field.metadata.inferredTypes
                    : []
                  const fieldWarnings = Array.isArray(field.metadata.warnings)
                    ? field.metadata.warnings
                    : []

                  return (
                    <tr
                      key={field.id}
                      className="align-top hover:bg-zinc-50/70 dark:hover:bg-zinc-800/30"
                    >
                      <td className="py-2.5 pr-2 pl-4 font-mono text-[11px] text-zinc-400 tabular-nums">
                        {field.ordinalPosition}
                      </td>
                      <td className="px-3 py-2.5">
                        <div
                          style={{ paddingLeft: `${depth * 12}px` }}
                          className="space-y-0.5"
                        >
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="font-medium text-zinc-900 dark:text-zinc-100">
                              {field.fieldName}
                            </span>
                            {field.metadata.isArray === true && (
                              <Badge
                                tone="slate"
                                className="px-1.5 py-0 text-[10px]"
                              >
                                array
                              </Badge>
                            )}
                          </div>
                          {field.fieldPath !== field.fieldName && (
                            <div className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
                              {field.fieldPath}
                            </div>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2.5">
                        <Badge tone={canonicalTypeTone(field.canonicalType)}>
                          {field.canonicalType}
                        </Badge>
                      </td>
                      <td className="px-3 py-2.5 font-mono text-[11px] text-zinc-700 dark:text-zinc-300">
                        {field.nativeType}
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex flex-wrap gap-1">
                          {field.isRequired && (
                            <Badge
                              tone="rose"
                              className="px-1.5 py-0 text-[10px]"
                            >
                              Required
                            </Badge>
                          )}
                          <Badge
                            tone={field.nullable ? 'zinc' : 'slate'}
                            className="px-1.5 py-0 text-[10px]"
                          >
                            {field.nullable ? 'Nullable' : 'Not Null'}
                          </Badge>
                        </div>
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex flex-wrap gap-1">
                          {field.primaryKey && (
                            <Badge
                              tone="emerald"
                              className="px-1.5 py-0 text-[10px]"
                            >
                              PK
                            </Badge>
                          )}
                          {field.uniqueKey && !field.primaryKey && (
                            <Badge
                              tone="slate"
                              className="px-1.5 py-0 text-[10px]"
                            >
                              Unique
                            </Badge>
                          )}
                          {fk && (
                            <span
                              className="inline-flex items-center rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0 font-mono text-[10px] text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
                              title={`References ${fk.targetObjectKey}.${fk.targetFieldPath}`}
                            >
                              FK → {fk.targetObjectKey}.{fk.targetFieldPath}
                            </span>
                          )}
                          {!field.primaryKey && !field.uniqueKey && !fk && (
                            <span className="text-[11px] text-zinc-400">—</span>
                          )}
                        </div>
                      </td>
                      <td className="py-2.5 pr-4 pl-3 text-[11px] text-zinc-600 dark:text-zinc-300">
                        <div className="space-y-1">
                          {field.defaultMetadata?.hasDefault && (
                            <div className="font-mono text-[11px] text-zinc-700 dark:text-zinc-300">
                              {field.defaultMetadata.isGenerated
                                ? 'generated'
                                : 'default'}
                              {field.defaultMetadata.expression
                                ? `: ${field.defaultMetadata.expression}`
                                : ''}
                            </div>
                          )}
                          {inferredTypes.length > 0 && (
                            <div className="text-amber-700 dark:text-amber-300">
                              Observed: {inferredTypes.join(', ')}
                            </div>
                          )}
                          {fieldWarnings.map((msg, idx) => (
                            <div
                              key={idx}
                              className="flex items-start gap-1 text-amber-700 dark:text-amber-300"
                            >
                              <AlertTriangle
                                className="mt-0.5 h-3 w-3 shrink-0"
                                aria-hidden="true"
                              />
                              <span>{msg}</span>
                            </div>
                          ))}
                          {!field.defaultMetadata?.hasDefault &&
                            inferredTypes.length === 0 &&
                            fieldWarnings.length === 0 && (
                              <span className="text-zinc-400">—</span>
                            )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>

      {total > limit && (
        <div className="flex items-center justify-between border-t border-zinc-100 px-4 py-2.5 text-[11px] text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          <span className="tabular-nums">
            Showing {rangeStart}–{rangeEnd} of {total} fields
          </span>
          <div className="flex items-center gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              disabled={!canPrev || isLoading}
              onClick={() => onPageChange(Math.max(0, offset - limit))}
              className="h-7 px-2 text-[11px]"
            >
              Prev
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={!canNext || isLoading}
              onClick={() => onPageChange(offset + limit)}
              className="h-7 px-2 text-[11px]"
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </Card>
  )
}
