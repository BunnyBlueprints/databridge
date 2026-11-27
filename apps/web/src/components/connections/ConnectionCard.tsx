import { useEffect, useRef, useState } from 'react'
import {
  AlertCircle,
  Clock,
  Eye,
  Info,
  MoreHorizontal,
  Pencil,
  PlugZap,
  Repeat,
  Trash2,
  X,
} from 'lucide-react'
import type { Connection, ConnectionStatus } from '../../types/connection'
import { ConnectorIcon } from '../ui/ConnectorIcon'

export interface ConnectionCardProps {
  connection: Connection
  onViewDetails?: (connection: Connection) => void
  onEdit?: (connection: Connection) => void
  onTestConnection?: (connection: Connection) => void
  onDelete?: (connection: Connection) => void
}

type ConnectionActionKey = 'view' | 'edit' | 'test' | 'delete'

const ACTION_LABELS: Record<ConnectionActionKey, string> = {
  view: 'View details',
  edit: 'Edit connection',
  test: 'Test connection',
  delete: 'Delete connection',
}

const STATUS_STYLE: Record<
  ConnectionStatus,
  { badge: string; dot: string }
> = {
  Connected: {
    badge:
      'border-emerald-200/80 bg-emerald-50 text-emerald-700 dark:border-emerald-800/70 dark:bg-emerald-950/60 dark:text-emerald-300',
    dot: 'bg-emerald-500 dark:bg-emerald-400',
  },
  Disconnected: {
    badge:
      'border-zinc-200 bg-zinc-100/80 text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800/90 dark:text-zinc-300',
    dot: 'bg-zinc-400 dark:bg-zinc-500',
  },
  Error: {
    badge:
      'border-rose-200/80 bg-rose-50/80 text-rose-700 dark:border-rose-800/70 dark:bg-rose-950/50 dark:text-rose-300',
    dot: 'bg-rose-500 dark:bg-rose-400',
  },
}

export function ConnectionCard({
  connection,
  onViewDetails,
  onEdit,
  onTestConnection,
  onDelete,
}: ConnectionCardProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [placeholderNotice, setPlaceholderNotice] = useState<string | null>(
    null,
  )
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return

    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false)
      }
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [menuOpen])

  const statusStyle = STATUS_STYLE[connection.status]

  const handleAction = (action: ConnectionActionKey) => {
    setMenuOpen(false)

    if (action === 'view' && onViewDetails) {
      setPlaceholderNotice(null)
      onViewDetails(connection)
      return
    }

    if (action === 'edit' && onEdit) {
      setPlaceholderNotice(null)
      onEdit(connection)
      return
    }

    if (action === 'delete' && onDelete) {
      setPlaceholderNotice(null)
      onDelete(connection)
      return
    }

    if (action === 'test' && onTestConnection) {
      setPlaceholderNotice(null)
      onTestConnection(connection)
      return
    }

    switch (action) {
      case 'view':
      case 'edit':
      case 'delete':
        break
      case 'test':
        onTestConnection?.(connection)
        break
    }

    setPlaceholderNotice(
      `Placeholder action: "${ACTION_LABELS[action]}" is a UI preview only until backend connector operations are connected.`,
    )
  }

  return (
    <article className="flex h-full flex-col justify-between rounded-lg border border-zinc-200/90 bg-white p-4 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-colors hover:border-zinc-300/90 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700">
      <div>
        {/* Top bar: Connector identity, status badge, contextual menu */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <ConnectorIcon type={connection.type} />
            <div className="min-w-0">
              <h3 className="truncate text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
                {connection.name}
              </h3>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                {connection.type}
              </p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            <span
              className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${statusStyle.badge}`}
            >
              <span
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusStyle.dot}`}
                aria-hidden="true"
              />
              {connection.status}
            </span>

            <div ref={menuRef} className="relative">
              <button
                type="button"
                onClick={() => setMenuOpen((prev) => !prev)}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                aria-label={`Actions for ${connection.name}`}
                className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md border border-transparent text-zinc-500 transition-colors hover:border-zinc-200 hover:bg-zinc-50 hover:text-zinc-900 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none dark:text-zinc-400 dark:hover:border-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
              >
                <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
              </button>

              {menuOpen && (
                <div
                  role="menu"
                  aria-label={`${connection.name} actions`}
                  className="absolute right-0 z-30 mt-1.5 w-44 rounded-md border border-zinc-200 bg-white p-1 shadow-md dark:border-zinc-700 dark:bg-zinc-900"
                >
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => handleAction('view')}
                    className="flex w-full cursor-pointer items-center gap-2 rounded px-2.5 py-1.5 text-left text-xs text-zinc-700 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:hover:text-white"
                  >
                    <Eye className="h-3.5 w-3.5 text-zinc-400" aria-hidden="true" />
                    <span>View details</span>
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => handleAction('edit')}
                    className="flex w-full cursor-pointer items-center gap-2 rounded px-2.5 py-1.5 text-left text-xs text-zinc-700 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:hover:text-white"
                  >
                    <Pencil className="h-3.5 w-3.5 text-zinc-400" aria-hidden="true" />
                    <span>Edit</span>
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => handleAction('test')}
                    className="flex w-full cursor-pointer items-center gap-2 rounded px-2.5 py-1.5 text-left text-xs text-zinc-700 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:hover:text-white"
                  >
                    <PlugZap className="h-3.5 w-3.5 text-zinc-400" aria-hidden="true" />
                    <span>Test connection</span>
                  </button>
                  <div className="my-1 border-t border-zinc-100 dark:border-zinc-800" />
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => handleAction('delete')}
                    className="flex w-full cursor-pointer items-center gap-2 rounded px-2.5 py-1.5 text-left text-xs text-rose-700 transition-colors hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/40"
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>Delete</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Endpoint & optional description */}
        <div className="mt-3 space-y-1.5">
          <div
            className="truncate rounded border border-zinc-200/70 bg-zinc-50/80 px-2.5 py-1.5 font-mono text-[11px] text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-300"
            title={connection.endpoint}
          >
            {connection.endpoint}
          </div>

          {connection.description && (
            <p className="line-clamp-2 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
              {connection.description}
            </p>
          )}

          {connection.errorMessage && (
            <div className="flex items-start gap-1.5 rounded border border-rose-200/70 bg-rose-50/50 px-2.5 py-1.5 text-[11px] text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300">
              <AlertCircle
                className="mt-0.5 h-3.5 w-3.5 shrink-0"
                aria-hidden="true"
              />
              <span>{connection.errorMessage}</span>
            </div>
          )}
        </div>
      </div>

      {/* Sync metadata footer + optional placeholder action feedback */}
      <div className="mt-4 space-y-2.5 border-t border-zinc-100 pt-3 dark:border-zinc-800">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
          <span className="inline-flex items-center gap-1.5 tabular-nums">
            <Clock className="h-3.5 w-3.5 text-zinc-400 dark:text-zinc-500" aria-hidden="true" />
            <span>
              Last sync:{' '}
              <strong className="font-medium text-zinc-700 dark:text-zinc-200">
                {connection.lastSuccessfulSync ?? '—'}
              </strong>
            </span>
          </span>

          <span className="inline-flex items-center gap-1.5 tabular-nums">
            <Repeat className="h-3.5 w-3.5 text-zinc-400 dark:text-zinc-500" aria-hidden="true" />
            <span>
              Frequency:{' '}
              <strong className="font-medium text-zinc-700 dark:text-zinc-200">
                {connection.syncFrequency ?? '—'}
              </strong>
            </span>
          </span>
        </div>

        {placeholderNotice && (
          <div
            role="status"
            className="flex items-start justify-between gap-2 rounded border border-zinc-200 bg-zinc-50 px-2.5 py-1.5 text-[11px] text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800/70 dark:text-zinc-300"
          >
            <div className="flex items-start gap-1.5">
              <Info
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-500 dark:text-zinc-400"
                aria-hidden="true"
              />
              <span>{placeholderNotice}</span>
            </div>
            <button
              type="button"
              onClick={() => setPlaceholderNotice(null)}
              aria-label="Dismiss action notice"
              className="cursor-pointer rounded p-0.5 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
            >
              <X className="h-3 w-3" aria-hidden="true" />
            </button>
          </div>
        )}
      </div>
    </article>
  )
}
