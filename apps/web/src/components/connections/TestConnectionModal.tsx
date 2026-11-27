import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  FileText,
  Loader2,
  PlugZap,
  ShieldCheck,
  Upload,
  X,
} from 'lucide-react'
import { testSavedConnection } from '../../services/connectionTestService.ts'
import type { Connection, ConnectionAuthType } from '../../types/connection.ts'
import type {
  ConnectionTestResult,
  ConnectionTestStatus,
} from '../../types/connectionTest.ts'
import { Button } from '../ui/Button.tsx'
import { ConnectorIcon } from '../ui/ConnectorIcon.tsx'
import {
  MAX_TEST_FILE_BYTES,
  formatFileSize,
  validateSelectedTestFile,
} from './connectionTestForm.ts'
import {
  extractFileName,
  parseSqlEndpoint,
  sanitizeDisplayEndpoint,
} from './connectorForm.ts'

export interface TestConnectionModalProps {
  connection: Connection | null
  onClose: () => void
  onTestCompleted?: (
    connectionId: string,
    result: ConnectionTestResult,
  ) => void
}

const AUTH_TYPE_LABEL: Record<ConnectionAuthType, string> = {
  none: 'None',
  api_key: 'API Key',
  bearer: 'Bearer Token',
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null
  return (
    <p id={id} className="mt-1 text-[11px] text-rose-600 dark:text-rose-400">
      {message}
    </p>
  )
}

function SummaryRow({
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

function SavedConnectionSummary({ connection }: { connection: Connection }) {
  const d = connection.details
  const cfg = connection.config

  const renderRows = () => {
    switch (connection.type) {
      case 'PostgreSQL':
      case 'MySQL': {
        const sqlCfg = cfg && 'host' in cfg ? cfg : undefined
        const parsed = parseSqlEndpoint(connection.endpoint)
        return (
          <>
            <SummaryRow
              label="Host"
              value={sqlCfg?.host ?? d?.host ?? parsed.host ?? 'Unavailable'}
              mono
            />
            <SummaryRow
              label="Port"
              value={
                sqlCfg?.port !== undefined
                  ? String(sqlCfg.port)
                  : d?.port !== undefined
                    ? String(d.port)
                    : (parsed.port ?? 'Unavailable')
              }
              mono
            />
            <SummaryRow
              label="Database"
              value={
                sqlCfg?.database ??
                d?.database ??
                parsed.database ??
                'Unavailable'
              }
              mono
            />
            <SummaryRow
              label="Credentials"
              value={
                d?.authConfigured
                  ? 'Encrypted in backend vault'
                  : 'Missing required credentials'
              }
            />
          </>
        )
      }

      case 'MongoDB': {
        const mongoCfg = cfg && 'uri' in cfg ? cfg : undefined
        return (
          <>
            <SummaryRow
              label="Endpoint"
              value={sanitizeDisplayEndpoint(
                d?.sanitizedUri ?? mongoCfg?.uri ?? connection.endpoint,
              )}
              mono
            />
            <SummaryRow
              label="Database"
              value={mongoCfg?.database ?? d?.database ?? 'Unavailable'}
              mono
            />
            <SummaryRow
              label="Credentials"
              value={
                d?.authConfigured
                  ? 'Encrypted in backend vault'
                  : 'None configured (passwordless)'
              }
            />
          </>
        )
      }

      case 'REST API': {
        const restCfg = cfg && 'baseUrl' in cfg ? cfg : undefined
        const authType = restCfg?.authType ?? d?.authType ?? 'none'
        return (
          <>
            <SummaryRow
              label="Base URL"
              value={sanitizeDisplayEndpoint(
                restCfg?.baseUrl ?? d?.baseUrl ?? connection.endpoint,
              )}
              mono
            />
            <SummaryRow
              label="Authentication"
              value={
                authType === 'none'
                  ? 'None'
                  : `${AUTH_TYPE_LABEL[authType]} (${
                      d?.authConfigured ? 'encrypted in vault' : 'missing'
                    })`
              }
            />
          </>
        )
      }

      case 'CSV':
      case 'JSON': {
        const fileCfg = cfg && 'fileName' in cfg ? cfg : undefined
        return (
          <SummaryRow
            label="Configured File"
            value={
              fileCfg?.fileName ??
              d?.fileName ??
              extractFileName(connection.endpoint) ??
              'Not specified'
            }
            mono
          />
        )
      }
    }
  }

  return (
    <dl className="divide-y divide-zinc-100 rounded-md border border-zinc-200/80 bg-zinc-50/40 px-3.5 dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-950/40">
      {renderRows()}
    </dl>
  )
}

function TestResultBanner({
  status,
  result,
  testedFileName,
  isFileConnector,
}: {
  status: ConnectionTestStatus
  result: ConnectionTestResult | null
  testedFileName: string | null
  isFileConnector: boolean
}) {
  if (status === 'idle') {
    return (
      <div className="flex items-start gap-2.5 rounded-md border border-zinc-200/80 bg-zinc-50/70 px-3.5 py-3 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-300">
        <ShieldCheck
          className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400"
          aria-hidden="true"
        />
        <div className="space-y-1">
          <p className="font-medium text-zinc-800 dark:text-zinc-200">
            Ready to test saved connection
          </p>
          <p className="text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
            {isFileConnector
              ? 'Select a local file to validate format and structure. File contents are sent transiently for this test only and are never saved.'
              : 'The backend will decrypt stored credentials securely in memory and test connectivity without exposing secrets.'}
          </p>
        </div>
      </div>
    )
  }

  if (status === 'testing') {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2.5 rounded-md border border-emerald-200/80 bg-emerald-50/60 px-3.5 py-3 text-xs text-emerald-900 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-200"
      >
        <Loader2
          className="h-4 w-4 shrink-0 animate-spin text-emerald-600 dark:text-emerald-400"
          aria-hidden="true"
        />
        <div>
          <div className="font-medium">Testing saved connection…</div>
          <p className="text-[11px] text-emerald-800/80 dark:text-emerald-300/80">
            Waiting for backend verification response.
          </p>
        </div>
      </div>
    )
  }

  if (!result) return null

  const isConnected = status === 'connected' && result.success
  const isTimeout = status === 'timeout'

  const tone = isConnected
    ? {
        wrapper:
          'border-emerald-200/90 bg-emerald-50/70 text-emerald-950 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-100',
        badge:
          'border-emerald-300/80 bg-emerald-100/80 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-200',
        label: 'Connected',
        Icon: CheckCircle2,
        iconClass: 'text-emerald-600 dark:text-emerald-400',
      }
    : isTimeout
      ? {
          wrapper:
            'border-amber-200/90 bg-amber-50/70 text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100',
          badge:
            'border-amber-300/80 bg-amber-100/80 text-amber-800 dark:border-amber-800 dark:bg-amber-900/60 dark:text-amber-200',
          label: 'Timed out',
          Icon: Clock,
          iconClass: 'text-amber-600 dark:text-amber-400',
        }
      : {
          wrapper:
            'border-rose-200/90 bg-rose-50/70 text-rose-950 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-100',
          badge:
            'border-rose-300/80 bg-rose-100/80 text-rose-800 dark:border-rose-800 dark:bg-rose-900/60 dark:text-rose-200',
          label: 'Failed',
          Icon: AlertCircle,
          iconClass: 'text-rose-600 dark:text-rose-400',
        }

  const { Icon } = tone

  return (
    <div
      role="status"
      aria-live="polite"
      className={`rounded-md border px-3.5 py-3 text-xs ${tone.wrapper}`}
    >
      <div className="flex items-start gap-2.5">
        <Icon
          className={`mt-0.5 h-4 w-4 shrink-0 ${tone.iconClass}`}
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-semibold ${tone.badge}`}
              >
                {tone.label}
              </span>
              {result.errorCode && (
                <code className="rounded border border-current/15 bg-white/60 px-1.5 py-0.5 font-mono text-[10px] dark:bg-zinc-900/60">
                  {result.errorCode}
                </code>
              )}
            </div>
            {result.durationMs !== undefined && (
              <span className="text-[11px] tabular-nums opacity-75">
                {result.durationMs} ms
              </span>
            )}
          </div>

          <p className="leading-relaxed">{result.message}</p>

          {testedFileName && (
            <p className="text-[11px] opacity-80">
              Verified transient file:{' '}
              <span className="font-mono">{testedFileName}</span> (file
              reference cleared after test).
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

export function TestConnectionModal({
  connection,
  onClose,
  onTestCompleted,
}: TestConnectionModalProps) {
  if (!connection) return null
  return (
    <TestConnectionDialog
      key={connection.id}
      connection={connection}
      onClose={onClose}
      onTestCompleted={onTestCompleted}
    />
  )
}

function TestConnectionDialog({
  connection,
  onClose,
  onTestCompleted,
}: {
  connection: Connection
  onClose: () => void
  onTestCompleted?: (
    connectionId: string,
    result: ConnectionTestResult,
  ) => void
}) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | undefined>(undefined)
  const [testStatus, setTestStatus] = useState<ConnectionTestStatus>('idle')
  const [result, setResult] = useState<ConnectionTestResult | null>(null)
  const [testedFileName, setTestedFileName] = useState<string | null>(null)
  const [cancelledNotice, setCancelledNotice] = useState<string | null>(null)

  const abortControllerRef = useRef<AbortController | null>(null)
  const cancelledByUserRef = useRef(false)
  const isMountedRef = useRef(true)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  const isTesting = testStatus === 'testing'
  const isFileConnector =
    connection.type === 'CSV' || connection.type === 'JSON'

  const clearTransientFile = useCallback(() => {
    setSelectedFile(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }, [])

  const handleClose = useCallback(() => {
    cancelledByUserRef.current = true
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    clearTransientFile()
    onClose()
  }, [clearTransientFile, onClose])

  useEffect(() => {
    isMountedRef.current = true
    const previouslyFocused =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
    closeButtonRef.current?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleClose()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      isMountedRef.current = false
      cancelledByUserRef.current = true
      abortControllerRef.current?.abort()
      abortControllerRef.current = null
      document.removeEventListener('keydown', handleKeyDown)
      if (previouslyFocused && document.contains(previouslyFocused)) {
        previouslyFocused.focus()
      }
    }
  }, [handleClose])

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null
    setCancelledNotice(null)
    setResult(null)
    setTestStatus('idle')
    setTestedFileName(null)

    if (!file || !isFileConnector) {
      setSelectedFile(null)
      return
    }

    const validationError = validateSelectedTestFile(
      file,
      connection.type as 'CSV' | 'JSON',
    )
    if (validationError) {
      clearTransientFile()
      setFileError(validationError)
      return
    }

    setSelectedFile(file)
    setFileError(undefined)
  }

  const handleCancelTest = () => {
    if (!abortControllerRef.current) return
    cancelledByUserRef.current = true
    abortControllerRef.current.abort()
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    if (isTesting || abortControllerRef.current !== null) {
      return
    }

    if (isFileConnector) {
      const validationError = validateSelectedTestFile(
        selectedFile,
        connection.type as 'CSV' | 'JSON',
      )
      if (validationError) {
        setFileError(validationError)
        return
      }
    }

    setFileError(undefined)
    setCancelledNotice(null)
    setResult(null)
    setTestedFileName(null)
    setTestStatus('testing')

    const controller = new AbortController()
    abortControllerRef.current = controller
    cancelledByUserRef.current = false

    const activeFile = selectedFile

    try {
      let filePayload: { fileName: string; fileContent: string } | undefined
      if (isFileConnector && activeFile) {
        const fileContent = await activeFile.text()
        const utf8Bytes = new Blob([fileContent]).size
        if (utf8Bytes > MAX_TEST_FILE_BYTES) {
          if (isMountedRef.current) {
            setTestStatus('idle')
            setFileError(
              `File content exceeds the 256 KB (${MAX_TEST_FILE_BYTES.toLocaleString()} bytes) limit.`,
            )
          }
          return
        }
        filePayload = { fileName: activeFile.name, fileContent }
      }

      const testResult = await testSavedConnection(
        connection.id,
        filePayload ? { fileContent: filePayload.fileContent } : undefined,
        { signal: controller.signal },
      )

      if (!isMountedRef.current) return

      if (controller.signal.aborted && cancelledByUserRef.current) {
        setTestStatus('idle')
        setResult(null)
        setCancelledNotice('Connection test was cancelled.')
        return
      }

      setResult(testResult)
      setTestStatus(testResult.status)
      if (filePayload) {
        setTestedFileName(filePayload.fileName)
      }
      onTestCompleted?.(connection.id, testResult)
    } catch {
      if (!isMountedRef.current) return
      setTestStatus('failed')
      setResult({
        success: false,
        status: 'failed',
        message: 'Unable to read the selected file or execute the test.',
        errorCode: 'INVALID_CONFIGURATION',
      })
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null
      }
      if (isMountedRef.current && isFileConnector) {
        clearTransientFile()
      }
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="test-connection-modal-title"
      aria-describedby="test-connection-modal-description"
    >
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-zinc-950/40 transition-opacity dark:bg-zinc-950/70"
        onClick={handleClose}
      />

      {/* Modal panel */}
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative z-10 flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-900"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-zinc-200/80 px-5 py-4 dark:border-zinc-800">
          <div className="flex min-w-0 items-center gap-3">
            <ConnectorIcon type={connection.type} />
            <div className="min-w-0">
              <h2
                id="test-connection-modal-title"
                className="truncate text-base font-semibold tracking-tight text-zinc-950 dark:text-zinc-50"
              >
                Test Connection · {connection.name}
              </h2>
              <p
                id="test-connection-modal-description"
                className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400"
              >
                {connection.type} backend verification
              </p>
            </div>
          </div>

          <button
            ref={closeButtonRef}
            type="button"
            onClick={handleClose}
            aria-label="Close Test Connection modal"
            className="inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Form body */}
        <form
          onSubmit={handleSubmit}
          noValidate
          className="flex min-h-0 flex-1 flex-col"
        >
          <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
            <SavedConnectionSummary connection={connection} />

            {isFileConnector && (
              <div>
                <span className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                  Select {connection.type} File to Validate{' '}
                  <span className="text-rose-600">*</span>
                </span>
                <label
                  htmlFor="test-conn-file-input"
                  className={`flex items-center justify-between gap-3 rounded-md border border-dashed px-3.5 py-3 transition-colors focus-within:ring-2 focus-within:ring-emerald-600 ${
                    isTesting
                      ? 'cursor-not-allowed opacity-60'
                      : 'cursor-pointer'
                  } ${
                    fileError
                      ? 'border-rose-400 bg-rose-50/40 dark:border-rose-700 dark:bg-rose-950/20'
                      : 'border-zinc-300 bg-zinc-50/60 hover:border-emerald-500 hover:bg-emerald-50/30 dark:border-zinc-700 dark:bg-zinc-950/60 dark:hover:border-emerald-600'
                  }`}
                >
                  <div className="flex min-w-0 items-center gap-2.5">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-zinc-200 bg-white text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                      {selectedFile ? (
                        <FileText
                          className="h-4 w-4 text-emerald-600"
                          aria-hidden="true"
                        />
                      ) : (
                        <Upload className="h-4 w-4" aria-hidden="true" />
                      )}
                    </span>
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-zinc-800 dark:text-zinc-200">
                        {selectedFile
                          ? selectedFile.name
                          : `Choose a ${connection.type === 'CSV' ? '.csv' : '.json'} file…`}
                      </div>
                      <div className="text-[11px] text-zinc-500 dark:text-zinc-400">
                        {selectedFile
                          ? `${formatFileSize(selectedFile.size)} · Read transiently on test only`
                          : 'Max 256 KB · Read only when you click Run Connection Test'}
                      </div>
                    </div>
                  </div>
                  <span className="shrink-0 rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200">
                    Browse
                  </span>
                </label>
                <input
                  ref={fileInputRef}
                  id="test-conn-file-input"
                  type="file"
                  disabled={isTesting}
                  accept={
                    connection.type === 'CSV'
                      ? '.csv,text/csv'
                      : '.json,application/json'
                  }
                  onChange={handleFileChange}
                  aria-invalid={Boolean(fileError)}
                  aria-describedby={
                    fileError ? 'test-conn-file-error' : undefined
                  }
                  className="sr-only"
                />
                <FieldError id="test-conn-file-error" message={fileError} />
              </div>
            )}

            {cancelledNotice && (
              <div
                role="status"
                className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800/70 dark:text-zinc-200"
              >
                {cancelledNotice}
              </div>
            )}

            <TestResultBanner
              status={testStatus}
              result={result}
              testedFileName={testedFileName}
              isFileConnector={isFileConnector}
            />
          </div>

          {/* Footer */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-200/80 bg-zinc-50/70 px-5 py-3 dark:border-zinc-800 dark:bg-zinc-950/60">
            <div className="flex items-center gap-2">
              {isTesting && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleCancelTest}
                >
                  Cancel Test
                </Button>
              )}
            </div>

            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" onClick={handleClose}>
                Close
              </Button>
              <Button
                variant="primary"
                size="sm"
                type="submit"
                disabled={isTesting}
              >
                {isTesting ? (
                  <>
                    <Loader2
                      className="h-3.5 w-3.5 animate-spin"
                      aria-hidden="true"
                    />
                    <span>Testing…</span>
                  </>
                ) : (
                  <>
                    <PlugZap className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>Run Connection Test</span>
                  </>
                )}
              </Button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
