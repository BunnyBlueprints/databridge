import { useEffect, useRef } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import type { Connection } from '../../types/connection'
import { Button } from '../ui/Button'
import { ConnectorIcon } from '../ui/ConnectorIcon'

export interface DeleteConnectionDialogProps {
  connection: Connection | null
  onCancel: () => void
  onConfirmDelete: (connection: Connection) => void
}

export function DeleteConnectionDialog({
  connection,
  onCancel,
  onConfirmDelete,
}: DeleteConnectionDialogProps) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!connection) return

    cancelButtonRef.current?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onCancel()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [connection, onCancel])

  if (!connection) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="delete-connection-title"
      aria-describedby="delete-connection-description"
    >
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-zinc-950/40 transition-opacity dark:bg-zinc-950/70"
        onClick={onCancel}
      />

      {/* Dialog panel */}
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
                id="delete-connection-title"
                className="text-base font-semibold tracking-tight text-zinc-950 dark:text-zinc-50"
              >
                Delete connection
              </h2>
              <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                Confirm removal from this workspace session
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={onCancel}
            aria-label="Cancel and close delete dialog"
            className="inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="space-y-3.5 px-5 py-4">
          <p
            id="delete-connection-description"
            className="text-xs leading-relaxed text-zinc-600 dark:text-zinc-300"
          >
            This removes the connection configuration for{' '}
            <strong className="font-semibold text-zinc-900 dark:text-zinc-100">
              {connection.name}
            </strong>{' '}
            from the current application state. No external data source or
            backend job is disconnected or modified.
          </p>

          <div className="flex items-center gap-3 rounded-md border border-zinc-200/80 bg-zinc-50/70 px-3 py-2.5 dark:border-zinc-800 dark:bg-zinc-950/60">
            <ConnectorIcon type={connection.type} size="sm" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                  {connection.name}
                </span>
                <span className="shrink-0 text-[11px] text-zinc-500 dark:text-zinc-400">
                  {connection.type}
                </span>
              </div>
              <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
                {connection.endpoint}
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-zinc-200/80 bg-zinc-50/70 px-5 py-3 dark:border-zinc-800 dark:bg-zinc-950/60">
          <Button
            ref={cancelButtonRef}
            variant="secondary"
            size="sm"
            onClick={onCancel}
            className="dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
          >
            Cancel
          </Button>
          <button
            type="button"
            onClick={() => onConfirmDelete(connection)}
            className="inline-flex h-8 cursor-pointer items-center justify-center rounded-md border border-rose-700/10 bg-rose-600 px-3 text-xs font-medium whitespace-nowrap text-white shadow-[0_1px_2px_rgba(225,29,72,0.18)] transition-colors hover:bg-rose-700 focus-visible:ring-2 focus-visible:ring-rose-600 focus-visible:ring-offset-1 focus-visible:outline-none dark:bg-rose-600 dark:hover:bg-rose-500"
          >
            Delete Connection
          </button>
        </div>
      </div>
    </div>
  )
}
