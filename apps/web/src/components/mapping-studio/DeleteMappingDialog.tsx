import { useEffect, useRef } from 'react'
import { AlertCircle, AlertTriangle, Loader2, Workflow, X } from 'lucide-react'
import type { MappingRecord } from '../../types/mapping.ts'
import { Button } from '../ui/Button.tsx'

export interface DeleteMappingDialogProps {
  mapping: MappingRecord | null
  isDeleting?: boolean
  errorMessage?: string | null
  onCancel: () => void
  onConfirmDelete: (mapping: MappingRecord) => void
}

export function DeleteMappingDialog({
  mapping,
  isDeleting = false,
  errorMessage = null,
  onCancel,
  onConfirmDelete,
}: DeleteMappingDialogProps) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!mapping) return
    cancelButtonRef.current?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !isDeleting) {
        onCancel()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [mapping, isDeleting, onCancel])

  if (!mapping) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="delete-mapping-title"
      aria-describedby="delete-mapping-description"
    >
      <div
        className="fixed inset-0 bg-zinc-950/40 transition-opacity dark:bg-zinc-950/70"
        onClick={() => {
          if (!isDeleting) onCancel()
        }}
      />

      <div
        onClick={(e) => e.stopPropagation()}
        className="relative z-10 w-full max-w-md overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-900"
      >
        <div className="flex items-start justify-between gap-3 border-b border-zinc-200/80 px-5 py-4 dark:border-zinc-800">
          <div className="flex items-start gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-rose-200/80 bg-rose-50 text-rose-600 dark:border-rose-900/70 dark:bg-rose-950/50 dark:text-rose-400">
              <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            </span>
            <div>
              <h2
                id="delete-mapping-title"
                className="text-base font-semibold tracking-tight text-zinc-950 dark:text-zinc-50"
              >
                Delete mapping
              </h2>
              <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                Confirm permanent removal of mapping configuration and rules
              </p>
            </div>
          </div>

          <button
            type="button"
            disabled={isDeleting}
            onClick={onCancel}
            aria-label="Cancel and close delete dialog"
            className="inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="space-y-3.5 px-5 py-4">
          {errorMessage && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-md border border-rose-200/90 bg-rose-50/80 px-3 py-2 text-xs text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-200"
            >
              <AlertCircle
                className="mt-0.5 h-4 w-4 shrink-0 text-rose-600 dark:text-rose-400"
                aria-hidden="true"
              />
              <span>{errorMessage}</span>
            </div>
          )}

          <p
            id="delete-mapping-description"
            className="text-xs leading-relaxed text-zinc-600 dark:text-zinc-300"
          >
            This permanently deletes{' '}
            <strong className="font-semibold text-zinc-900 dark:text-zinc-100">
              {mapping.name}
            </strong>{' '}
            and all {mapping.rules.length} field mapping rule
            {mapping.rules.length === 1 ? '' : 's'} from backend storage. Source
            and destination schemas remain untouched.
          </p>

          <div className="flex items-center gap-3 rounded-md border border-zinc-200/80 bg-zinc-50/70 px-3 py-2.5 dark:border-zinc-800 dark:bg-zinc-950/60">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-zinc-200 bg-white text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
              <Workflow className="h-3.5 w-3.5" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1 text-xs">
              <div className="truncate font-semibold text-zinc-900 dark:text-zinc-100">
                {mapping.name} (v{mapping.mappingVersion})
              </div>
              <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
                {mapping.sourceObjectKey} → {mapping.destinationObjectKey}
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-zinc-200/80 bg-zinc-50/70 px-5 py-3 dark:border-zinc-800 dark:bg-zinc-950/60">
          <Button
            ref={cancelButtonRef}
            variant="secondary"
            size="sm"
            disabled={isDeleting}
            onClick={onCancel}
            className="dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
          >
            Cancel
          </Button>
          <button
            type="button"
            disabled={isDeleting}
            onClick={() => onConfirmDelete(mapping)}
            className="inline-flex h-8 cursor-pointer items-center justify-center gap-1.5 rounded-md border border-rose-700/10 bg-rose-600 px-3 text-xs font-medium whitespace-nowrap text-white shadow-[0_1px_2px_rgba(225,29,72,0.18)] transition-colors hover:bg-rose-700 focus-visible:ring-2 focus-visible:ring-rose-600 focus-visible:ring-offset-1 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60 dark:bg-rose-600 dark:hover:bg-rose-500"
          >
            {isDeleting && (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            )}
            <span>{isDeleting ? 'Deleting…' : 'Delete Mapping'}</span>
          </button>
        </div>
      </div>
    </div>
  )
}
