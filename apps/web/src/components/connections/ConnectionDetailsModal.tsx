import { useEffect, useState } from 'react'
import {
  AlertCircle,
  Clock,
  Info,
  Pencil,
  PlugZap,
  Repeat,
  ShieldCheck,
  X,
} from 'lucide-react'
import type {
  Connection,
  ConnectionAuthType,
  ConnectionStatus,
} from '../../types/connection'
import { Button } from '../ui/Button'
import { ConnectorIcon } from '../ui/ConnectorIcon'
import {
  extractFileName,
  parseMongoEndpoint,
  parseSqlEndpoint,
  sanitizeDisplayEndpoint,
} from './connectorForm'

export interface ConnectionDetailsModalProps {
  connection: Connection | null
  onClose: () => void
  onEdit?: (connection: Connection) => void
  onTestConnection?: (connection: Connection) => void
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

const AUTH_TYPE_LABEL: Record<ConnectionAuthType, string> = {
  none: 'None',
  api_key: 'API Key',
  bearer: 'Bearer Token',
}

function DetailRow({
  label,
  value,
  mono = false,
}: {
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div className="flex flex-col gap-0.5 py-2 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
      <dt className="shrink-0 text-xs text-zinc-500 dark:text-zinc-400">
        {label}
      </dt>
      <dd
        className={`min-w-0 break-all text-xs font-medium text-zinc-900 sm:text-right dark:text-zinc-100 ${
          mono ? 'font-mono text-[11px]' : ''
        }`}
      >
        {value}
      </dd>
    </div>
  )
}

function ConfigurationSection({ connection }: { connection: Connection }) {
  const d = connection.details

  switch (connection.type) {
    case 'PostgreSQL':
    case 'MySQL': {
      const parsed = parseSqlEndpoint(connection.endpoint)
      const host = d?.host ?? parsed.host ?? 'Unavailable'
      const port = d?.port !== undefined ? String(d.port) : (parsed.port ?? 'Unavailable')
      const database = d?.database ?? parsed.database ?? 'Unavailable'
      const username = d?.username ?? 'Not recorded'

      return (
        <>
          <DetailRow label="Host" value={host} mono />
          <DetailRow label="Port" value={port} mono />
          <DetailRow label="Database Name" value={database} mono />
          <DetailRow label="Username" value={username} mono={Boolean(d?.username)} />
        </>
      )
    }

    case 'MongoDB': {
      const parsed = parseMongoEndpoint(connection.endpoint)
      const sanitizedUri = sanitizeDisplayEndpoint(
        d?.sanitizedUri ?? parsed.sanitizedUri,
      )
      const database = d?.database ?? parsed.database ?? 'Unavailable'
      const username = d?.username ?? 'Not specified'

      return (
        <>
          <DetailRow label="Sanitized Endpoint" value={sanitizedUri} mono />
          <DetailRow label="Database Name" value={database} mono />
          <DetailRow label="Username" value={username} mono={Boolean(d?.username)} />
        </>
      )
    }

    case 'REST API': {
      const baseUrl = sanitizeDisplayEndpoint(d?.baseUrl ?? connection.endpoint)
      const authLabel = d?.authType
        ? AUTH_TYPE_LABEL[d.authType]
        : 'Not recorded'

      return (
        <>
          <DetailRow label="Base URL" value={baseUrl} mono />
          <DetailRow label="Authentication Type" value={authLabel} />
        </>
      )
    }

    case 'CSV':
    case 'JSON': {
      const fileName =
        d?.fileName ?? extractFileName(connection.endpoint) ?? 'Not specified'

      return (
        <>
          <DetailRow
            label="Source Filename"
            value={fileName}
            mono={fileName !== 'Not specified'}
          />
          <DetailRow label="File Type" value={connection.type} />
        </>
      )
    }
  }
}

function getSecuritySummary(connection: Connection): {
  statusLabel: string
  detailNote: string
} {
  const d = connection.details

  if (d?.authConfigured === true) {
    const methodSuffix = d.authType ? ` (${AUTH_TYPE_LABEL[d.authType]})` : ''
    return {
      statusLabel: `Configured${methodSuffix}`,
      detailNote:
        'Credential secrets are masked on entry and never stored in or reconstructed by the UI.',
    }
  }

  if (d?.authConfigured === false) {
    return {
      statusLabel:
        connection.type === 'CSV' || connection.type === 'JSON'
          ? 'Not applicable (local file)'
          : 'None configured',
      detailNote:
        'No authentication secret is attached to this connector configuration.',
    }
  }

  return {
    statusLabel: 'Unavailable in mock metadata',
    detailNote:
      'Credential status is omitted because this prototype record does not carry backend vault metadata.',
  }
}

export function ConnectionDetailsModal({
  connection,
  onClose,
  onEdit,
  onTestConnection,
}: ConnectionDetailsModalProps) {
  if (!connection) return null
  return (
    <ConnectionDetailsDialog
      key={connection.id}
      connection={connection}
      onClose={onClose}
      onEdit={onEdit}
      onTestConnection={onTestConnection}
    />
  )
}

function ConnectionDetailsDialog({
  connection,
  onClose,
  onEdit,
  onTestConnection,
}: {
  connection: Connection
  onClose: () => void
  onEdit?: (connection: Connection) => void
  onTestConnection?: (connection: Connection) => void
}) {
  const [actionNotice, setActionNotice] = useState<string | null>(null)

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  const statusStyle = STATUS_STYLE[connection.status]
  const security = getSecuritySummary(connection)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="connection-details-title"
      aria-describedby="connection-details-subtitle"
    >
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-zinc-950/40 transition-opacity dark:bg-zinc-950/70"
        onClick={onClose}
      />

      {/* Modal panel */}
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative z-10 flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-900"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-zinc-200/80 px-5 py-4 dark:border-zinc-800">
          <div className="flex min-w-0 items-center gap-3">
            <ConnectorIcon type={connection.type} />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2
                  id="connection-details-title"
                  className="truncate text-base font-semibold tracking-tight text-zinc-950 dark:text-zinc-50"
                >
                  {connection.name}
                </h2>
                <span
                  className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${statusStyle.badge}`}
                >
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusStyle.dot}`}
                    aria-hidden="true"
                  />
                  {connection.status}
                </span>
              </div>
              <p
                id="connection-details-subtitle"
                className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400"
              >
                {connection.type} connector details
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            aria-label="Close Connection Details modal"
            className="inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {connection.status === 'Error' && connection.errorMessage && (
            <div className="flex items-start gap-2 rounded-md border border-rose-200/80 bg-rose-50/70 px-3 py-2.5 text-xs text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300">
              <AlertCircle
                className="mt-0.5 h-4 w-4 shrink-0"
                aria-hidden="true"
              />
              <div>
                <div className="font-semibold">Latest connection error</div>
                <p className="mt-0.5">{connection.errorMessage}</p>
              </div>
            </div>
          )}

          {/* Overview */}
          <section aria-labelledby="details-overview-heading">
            <h3
              id="details-overview-heading"
              className="mb-1.5 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase"
            >
              Overview
            </h3>
            <dl className="divide-y divide-zinc-100 rounded-md border border-zinc-200/80 bg-zinc-50/40 px-3.5 dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-950/40">
              <DetailRow label="Connection Name" value={connection.name} />
              <DetailRow label="Connector Type" value={connection.type} />
              <DetailRow label="Connection ID" value={connection.id} mono />
              <DetailRow label="Status" value={connection.status} />
              {connection.description && (
                <DetailRow
                  label="Description"
                  value={connection.description}
                />
              )}
            </dl>
          </section>

          {/* Configuration */}
          <section aria-labelledby="details-config-heading">
            <h3
              id="details-config-heading"
              className="mb-1.5 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase"
            >
              Configuration
            </h3>
            <dl className="divide-y divide-zinc-100 rounded-md border border-zinc-200/80 bg-zinc-50/40 px-3.5 dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-950/40">
              <ConfigurationSection connection={connection} />
            </dl>
          </section>

          {/* Synchronization */}
          <section aria-labelledby="details-sync-heading">
            <h3
              id="details-sync-heading"
              className="mb-1.5 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase"
            >
              Synchronization
            </h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-md border border-zinc-200/80 bg-zinc-50/40 px-3.5 py-2.5 dark:border-zinc-800 dark:bg-zinc-950/40">
                <div className="flex items-center gap-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                  <Clock className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Last Successful Sync</span>
                </div>
                <div className="mt-1 text-xs font-medium text-zinc-900 tabular-nums dark:text-zinc-100">
                  {connection.lastSuccessfulSync ?? 'Not synced yet'}
                </div>
              </div>

              <div className="rounded-md border border-zinc-200/80 bg-zinc-50/40 px-3.5 py-2.5 dark:border-zinc-800 dark:bg-zinc-950/40">
                <div className="flex items-center gap-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                  <Repeat className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Sync Frequency</span>
                </div>
                <div className="mt-1 text-xs font-medium text-zinc-900 tabular-nums dark:text-zinc-100">
                  {connection.syncFrequency ?? 'Not configured'}
                </div>
              </div>
            </div>
          </section>

          {/* Security */}
          <section aria-labelledby="details-security-heading">
            <h3
              id="details-security-heading"
              className="mb-1.5 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase"
            >
              Security
            </h3>
            <div className="rounded-md border border-zinc-200/80 bg-zinc-50/40 px-3.5 py-2.5 dark:border-zinc-800 dark:bg-zinc-950/40">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="inline-flex items-center gap-1.5 text-zinc-500 dark:text-zinc-400">
                  <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Authentication</span>
                </span>
                <span className="font-medium text-zinc-900 dark:text-zinc-100">
                  {security.statusLabel}
                </span>
              </div>
              <p className="mt-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                {security.detailNote}
              </p>
            </div>
          </section>

          {/* Honest action placeholder feedback */}
          {actionNotice && (
            <div
              role="status"
              className="flex items-start justify-between gap-2.5 rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2.5 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800/70 dark:text-zinc-200"
            >
              <div className="flex items-start gap-2">
                <Info
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-500 dark:text-zinc-400"
                  aria-hidden="true"
                />
                <span>{actionNotice}</span>
              </div>
              <button
                type="button"
                onClick={() => setActionNotice(null)}
                aria-label="Dismiss placeholder notice"
                className="cursor-pointer rounded p-0.5 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          )}
        </div>

        {/* Footer actions */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-200/80 bg-zinc-50/70 px-5 py-3 dark:border-zinc-800 dark:bg-zinc-950/60">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                if (onEdit) {
                  onEdit(connection)
                  return
                }
                setActionNotice(
                  'Edit Connection is a UI placeholder. Updating saved connector settings requires backend integration.',
                )
              }}
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Edit Connection</span>
            </Button>

            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                if (onTestConnection) {
                  onTestConnection(connection)
                  return
                }
                setActionNotice(
                  'Test Connection is not implemented yet. No live network or database check was performed.',
                )
              }}
            >
              <PlugZap className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Test Connection</span>
            </Button>
          </div>

          <Button variant="primary" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  )
}
