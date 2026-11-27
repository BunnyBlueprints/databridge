import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Eye,
  EyeOff,
  FileText,
  Loader2,
  PlugZap,
  ShieldAlert,
  Upload,
  X,
} from 'lucide-react'
import { testConnection } from '../../services/connectionTestService'
import type { Connection, ConnectionAuthType } from '../../types/connection'
import type {
  ConnectionTestResult,
  ConnectionTestStatus,
} from '../../types/connectionTest'
import { Button } from '../ui/Button'
import { ConnectorIcon } from '../ui/ConnectorIcon'
import {
  MAX_TEST_FILE_BYTES,
  buildConnectionTestRequest,
  createInitialTestValues,
  formatFileSize,
  validateConnectionTestInputs,
  validateSelectedTestFile,
} from './connectionTestForm'
import type {
  ConnectionTestFormErrors,
  ConnectionTestFormValues,
} from './connectionTestForm'

export interface TestConnectionModalProps {
  connection: Connection | null
  onClose: () => void
}

const INPUT_BASE =
  'h-9 w-full rounded-md border bg-white px-3 text-xs text-zinc-900 shadow-[0_1px_2px_rgba(15,23,42,0.03)] placeholder:text-zinc-400 focus:ring-1 focus:outline-none disabled:cursor-not-allowed disabled:bg-zinc-50 disabled:text-zinc-500 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500'

function getInputBorder(hasError: boolean): string {
  return hasError
    ? 'border-rose-400 focus:border-rose-500 focus:ring-rose-500 dark:border-rose-700'
    : 'border-zinc-200 focus:border-emerald-600 focus:ring-emerald-600 dark:border-zinc-700 dark:focus:border-emerald-500'
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null
  return (
    <p id={id} className="mt-1 text-[11px] text-rose-600 dark:text-rose-400">
      {message}
    </p>
  )
}

function TestResultBanner({
  status,
  result,
  testedFileName,
}: {
  status: ConnectionTestStatus
  result: ConnectionTestResult | null
  testedFileName: string | null
}) {
  if (status === 'idle') {
    return (
      <div className="flex items-start gap-2.5 rounded-md border border-zinc-200/80 bg-zinc-50/70 px-3.5 py-3 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-300">
        <ShieldAlert
          className="mt-0.5 h-4 w-4 shrink-0 text-zinc-400 dark:text-zinc-500"
          aria-hidden="true"
        />
        <div className="space-y-1">
          <p className="font-medium text-zinc-800 dark:text-zinc-200">
            Ready to test connection
          </p>
          <p className="text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
            Credentials and file contents are transient for this check only and
            are never saved to browser storage or a credential vault.
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
          <div className="font-medium">Testing connection…</div>
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
}: TestConnectionModalProps) {
  if (!connection) return null
  return (
    <TestConnectionDialog
      key={connection.id}
      connection={connection}
      onClose={onClose}
    />
  )
}

function TestConnectionDialog({
  connection,
  onClose,
}: {
  connection: Connection
  onClose: () => void
}) {
  const [values, setValues] = useState<ConnectionTestFormValues>(() =>
    createInitialTestValues(connection),
  )
  const [errors, setErrors] = useState<ConnectionTestFormErrors>({})
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [testStatus, setTestStatus] = useState<ConnectionTestStatus>('idle')
  const [result, setResult] = useState<ConnectionTestResult | null>(null)
  const [testedFileName, setTestedFileName] = useState<string | null>(null)
  const [cancelledNotice, setCancelledNotice] = useState<string | null>(null)
  const [showSecret, setShowSecret] = useState(false)

  const abortControllerRef = useRef<AbortController | null>(null)
  const cancelledByUserRef = useRef(false)
  const isMountedRef = useRef(true)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  const isTesting = testStatus === 'testing'
  const isFileConnector =
    connection.type === 'CSV' || connection.type === 'JSON'

  const clearTransientInputs = useCallback(() => {
    setSelectedFile(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
    setValues((prev) => ({
      ...prev,
      password: '',
      mongoPassword: '',
      mongoConnectionUri: '',
      apiKey: '',
      bearerToken: '',
    }))
  }, [])

  const handleClose = useCallback(() => {
    cancelledByUserRef.current = true
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    clearTransientInputs()
    onClose()
  }, [clearTransientInputs, onClose])

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

  const handleFieldChange = <K extends keyof ConnectionTestFormValues>(
    field: K,
    value: ConnectionTestFormValues[K],
  ) => {
    setValues((prev) => {
      if (field === 'authType') {
        return {
          ...prev,
          authType: value as ConnectionAuthType,
          apiKey: '',
          bearerToken: '',
        }
      }
      return { ...prev, [field]: value }
    })
    setCancelledNotice(null)
    setErrors((prev) => {
      const next = { ...prev }
      delete next[field]
      if (field === 'authType') {
        delete next.apiKey
        delete next.bearerToken
      }
      return next
    })
  }

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

    const fileError = validateSelectedTestFile(file, connection.type as 'CSV' | 'JSON')
    if (fileError) {
      setSelectedFile(null)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
      setErrors((prev) => ({ ...prev, file: fileError }))
      return
    }

    setSelectedFile(file)
    setErrors((prev) => {
      if (!prev.file) return prev
      const next = { ...prev }
      delete next.file
      return next
    })
  }

  const handleCancelTest = () => {
    if (!abortControllerRef.current) return
    cancelledByUserRef.current = true
    abortControllerRef.current.abort()
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    // Prevent accidental duplicate requests while a test is already running.
    if (isTesting || abortControllerRef.current !== null) {
      return
    }

    const validationErrors = validateConnectionTestInputs(
      connection,
      values,
      selectedFile,
    )
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors)
      return
    }

    setErrors({})
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
            setErrors({
              file: `File content exceeds the 256 KB (${MAX_TEST_FILE_BYTES.toLocaleString()} bytes) limit.`,
            })
          }
          return
        }
        filePayload = { fileName: activeFile.name, fileContent }
      }

      const request = buildConnectionTestRequest(
        connection,
        values,
        filePayload,
      )
      const testResult = await testConnection(request, {
        signal: controller.signal,
      })

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
        setSelectedFile(null)
        if (fileInputRef.current) {
          fileInputRef.current.value = ''
        }
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
                {connection.type} connectivity check
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
            {(connection.type === 'PostgreSQL' ||
              connection.type === 'MySQL') && (
              <div className="space-y-3">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div className="sm:col-span-2">
                    <label
                      htmlFor="test-conn-host"
                      className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      Host <span className="text-rose-600">*</span>
                    </label>
                    <input
                      id="test-conn-host"
                      type="text"
                      disabled={isTesting}
                      value={values.host}
                      onChange={(e) =>
                        handleFieldChange('host', e.target.value)
                      }
                      placeholder="db.internal.example.com"
                      aria-invalid={Boolean(errors.host)}
                      aria-describedby={
                        errors.host ? 'test-conn-host-error' : undefined
                      }
                      className={`${INPUT_BASE} ${getInputBorder(Boolean(errors.host))}`}
                    />
                    <FieldError
                      id="test-conn-host-error"
                      message={errors.host}
                    />
                  </div>

                  <div>
                    <label
                      htmlFor="test-conn-port"
                      className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      Port <span className="text-rose-600">*</span>
                    </label>
                    <input
                      id="test-conn-port"
                      type="text"
                      inputMode="numeric"
                      disabled={isTesting}
                      value={values.port}
                      onChange={(e) =>
                        handleFieldChange('port', e.target.value)
                      }
                      aria-invalid={Boolean(errors.port)}
                      aria-describedby={
                        errors.port ? 'test-conn-port-error' : undefined
                      }
                      className={`${INPUT_BASE} tabular-nums ${getInputBorder(Boolean(errors.port))}`}
                    />
                    <FieldError
                      id="test-conn-port-error"
                      message={errors.port}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <label
                      htmlFor="test-conn-database"
                      className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      Database <span className="text-rose-600">*</span>
                    </label>
                    <input
                      id="test-conn-database"
                      type="text"
                      disabled={isTesting}
                      value={values.database}
                      onChange={(e) =>
                        handleFieldChange('database', e.target.value)
                      }
                      aria-invalid={Boolean(errors.database)}
                      aria-describedby={
                        errors.database ? 'test-conn-db-error' : undefined
                      }
                      className={`${INPUT_BASE} ${getInputBorder(Boolean(errors.database))}`}
                    />
                    <FieldError
                      id="test-conn-db-error"
                      message={errors.database}
                    />
                  </div>

                  <div>
                    <label
                      htmlFor="test-conn-username"
                      className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      Username <span className="text-rose-600">*</span>
                    </label>
                    <input
                      id="test-conn-username"
                      type="text"
                      autoComplete="off"
                      disabled={isTesting}
                      value={values.username}
                      onChange={(e) =>
                        handleFieldChange('username', e.target.value)
                      }
                      placeholder="databridge_ro"
                      aria-invalid={Boolean(errors.username)}
                      aria-describedby={
                        errors.username ? 'test-conn-user-error' : undefined
                      }
                      className={`${INPUT_BASE} ${getInputBorder(Boolean(errors.username))}`}
                    />
                    <FieldError
                      id="test-conn-user-error"
                      message={errors.username}
                    />
                  </div>
                </div>

                <div>
                  <label
                    htmlFor="test-conn-password"
                    className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Password{' '}
                    <span className="font-normal text-zinc-400">
                      (transient, optional for passwordless auth)
                    </span>
                  </label>
                  <div className="relative">
                    <input
                      id="test-conn-password"
                      type={showSecret ? 'text' : 'password'}
                      autoComplete="new-password"
                      disabled={isTesting}
                      value={values.password}
                      onChange={(e) =>
                        handleFieldChange('password', e.target.value)
                      }
                      placeholder="Enter password for this test"
                      className={`${INPUT_BASE} pr-9 ${getInputBorder(false)}`}
                    />
                    <button
                      type="button"
                      onClick={() => setShowSecret((prev) => !prev)}
                      aria-label={
                        showSecret ? 'Hide password' : 'Show password'
                      }
                      className="absolute top-1/2 right-2 -translate-y-1/2 cursor-pointer rounded p-1 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
                    >
                      {showSecret ? (
                        <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
                      ) : (
                        <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                      )}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {connection.type === 'MongoDB' && (
              <div className="space-y-3">
                <div>
                  <label
                    htmlFor="test-mongo-uri"
                    className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Connection URI <span className="text-rose-600">*</span>
                  </label>
                  <input
                    id="test-mongo-uri"
                    type="text"
                    autoComplete="off"
                    disabled={isTesting}
                    value={values.mongoUri}
                    onChange={(e) =>
                      handleFieldChange('mongoUri', e.target.value)
                    }
                    placeholder="mongodb+srv://cluster01.mongodb.net"
                    aria-invalid={Boolean(errors.mongoUri)}
                    aria-describedby={
                      errors.mongoUri ? 'test-mongo-uri-error' : undefined
                    }
                    className={`${INPUT_BASE} font-mono ${getInputBorder(Boolean(errors.mongoUri))}`}
                  />
                  <FieldError
                    id="test-mongo-uri-error"
                    message={errors.mongoUri}
                  />
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <label
                      htmlFor="test-mongo-db"
                      className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      Database <span className="text-rose-600">*</span>
                    </label>
                    <input
                      id="test-mongo-db"
                      type="text"
                      disabled={isTesting}
                      value={values.mongoDatabase}
                      onChange={(e) =>
                        handleFieldChange('mongoDatabase', e.target.value)
                      }
                      aria-invalid={Boolean(errors.mongoDatabase)}
                      aria-describedby={
                        errors.mongoDatabase ? 'test-mongo-db-error' : undefined
                      }
                      className={`${INPUT_BASE} ${getInputBorder(Boolean(errors.mongoDatabase))}`}
                    />
                    <FieldError
                      id="test-mongo-db-error"
                      message={errors.mongoDatabase}
                    />
                  </div>

                  <div>
                    <label
                      htmlFor="test-mongo-user"
                      className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      Username{' '}
                      <span className="font-normal text-zinc-400">
                        (optional)
                      </span>
                    </label>
                    <input
                      id="test-mongo-user"
                      type="text"
                      autoComplete="off"
                      disabled={isTesting}
                      value={values.mongoUsername}
                      onChange={(e) =>
                        handleFieldChange('mongoUsername', e.target.value)
                      }
                      placeholder="reader"
                      className={`${INPUT_BASE} ${getInputBorder(false)}`}
                    />
                  </div>
                </div>

                <div>
                  <label
                    htmlFor="test-mongo-password"
                    className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Password{' '}
                    <span className="font-normal text-zinc-400">
                      (transient, optional)
                    </span>
                  </label>
                  <div className="relative">
                    <input
                      id="test-mongo-password"
                      type={showSecret ? 'text' : 'password'}
                      autoComplete="new-password"
                      disabled={isTesting}
                      value={values.mongoPassword}
                      onChange={(e) =>
                        handleFieldChange('mongoPassword', e.target.value)
                      }
                      placeholder="Enter password if required"
                      className={`${INPUT_BASE} pr-9 ${getInputBorder(false)}`}
                    />
                    <button
                      type="button"
                      onClick={() => setShowSecret((prev) => !prev)}
                      aria-label={
                        showSecret ? 'Hide password' : 'Show password'
                      }
                      className="absolute top-1/2 right-2 -translate-y-1/2 cursor-pointer rounded p-1 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
                    >
                      {showSecret ? (
                        <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
                      ) : (
                        <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                      )}
                    </button>
                  </div>
                </div>

                <div>
                  <label
                    htmlFor="test-mongo-conn-uri"
                    className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Full Connection URI with Credentials{' '}
                    <span className="font-normal text-zinc-400">
                      (optional transient override)
                    </span>
                  </label>
                  <input
                    id="test-mongo-conn-uri"
                    type="password"
                    autoComplete="off"
                    disabled={isTesting}
                    value={values.mongoConnectionUri}
                    onChange={(e) =>
                      handleFieldChange('mongoConnectionUri', e.target.value)
                    }
                    placeholder="mongodb+srv://user:password@cluster01.mongodb.net"
                    aria-invalid={Boolean(errors.mongoConnectionUri)}
                    aria-describedby={
                      errors.mongoConnectionUri
                        ? 'test-mongo-conn-uri-error'
                        : undefined
                    }
                    className={`${INPUT_BASE} font-mono ${getInputBorder(Boolean(errors.mongoConnectionUri))}`}
                  />
                  <FieldError
                    id="test-mongo-conn-uri-error"
                    message={errors.mongoConnectionUri}
                  />
                </div>
              </div>
            )}

            {connection.type === 'REST API' && (
              <div className="space-y-3">
                <div>
                  <label
                    htmlFor="test-rest-url"
                    className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Base URL <span className="text-rose-600">*</span>
                  </label>
                  <input
                    id="test-rest-url"
                    type="url"
                    disabled={isTesting}
                    value={values.baseUrl}
                    onChange={(e) =>
                      handleFieldChange('baseUrl', e.target.value)
                    }
                    placeholder="https://api.example.com/v2"
                    aria-invalid={Boolean(errors.baseUrl)}
                    aria-describedby={
                      errors.baseUrl ? 'test-rest-url-error' : undefined
                    }
                    className={`${INPUT_BASE} font-mono ${getInputBorder(Boolean(errors.baseUrl))}`}
                  />
                  <FieldError
                    id="test-rest-url-error"
                    message={errors.baseUrl}
                  />
                </div>

                <div>
                  <label
                    htmlFor="test-rest-auth"
                    className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Authentication Type
                  </label>
                  <select
                    id="test-rest-auth"
                    disabled={isTesting}
                    value={values.authType}
                    onChange={(e) =>
                      handleFieldChange(
                        'authType',
                        e.target.value as ConnectionAuthType,
                      )
                    }
                    className={`${INPUT_BASE} ${getInputBorder(false)}`}
                  >
                    <option value="none">None</option>
                    <option value="api_key">API Key (X-API-Key header)</option>
                    <option value="bearer">Bearer Token</option>
                  </select>
                </div>

                {values.authType === 'api_key' && (
                  <div>
                    <label
                      htmlFor="test-rest-api-key"
                      className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      API Key <span className="text-rose-600">*</span>
                    </label>
                    <div className="relative">
                      <input
                        id="test-rest-api-key"
                        type={showSecret ? 'text' : 'password'}
                        autoComplete="off"
                        disabled={isTesting}
                        value={values.apiKey}
                        onChange={(e) =>
                          handleFieldChange('apiKey', e.target.value)
                        }
                        placeholder="Sent in X-API-Key header for this test only"
                        aria-invalid={Boolean(errors.apiKey)}
                        aria-describedby={
                          errors.apiKey ? 'test-rest-api-key-error' : undefined
                        }
                        className={`${INPUT_BASE} pr-9 font-mono ${getInputBorder(Boolean(errors.apiKey))}`}
                      />
                      <button
                        type="button"
                        onClick={() => setShowSecret((prev) => !prev)}
                        aria-label={
                          showSecret ? 'Hide API key' : 'Show API key'
                        }
                        className="absolute top-1/2 right-2 -translate-y-1/2 cursor-pointer rounded p-1 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
                      >
                        {showSecret ? (
                          <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
                        ) : (
                          <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                        )}
                      </button>
                    </div>
                    <FieldError
                      id="test-rest-api-key-error"
                      message={errors.apiKey}
                    />
                  </div>
                )}

                {values.authType === 'bearer' && (
                  <div>
                    <label
                      htmlFor="test-rest-bearer"
                      className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      Bearer Token <span className="text-rose-600">*</span>
                    </label>
                    <div className="relative">
                      <input
                        id="test-rest-bearer"
                        type={showSecret ? 'text' : 'password'}
                        autoComplete="off"
                        disabled={isTesting}
                        value={values.bearerToken}
                        onChange={(e) =>
                          handleFieldChange('bearerToken', e.target.value)
                        }
                        placeholder="Sent in Authorization: Bearer header for this test only"
                        aria-invalid={Boolean(errors.bearerToken)}
                        aria-describedby={
                          errors.bearerToken
                            ? 'test-rest-bearer-error'
                            : undefined
                        }
                        className={`${INPUT_BASE} pr-9 font-mono ${getInputBorder(Boolean(errors.bearerToken))}`}
                      />
                      <button
                        type="button"
                        onClick={() => setShowSecret((prev) => !prev)}
                        aria-label={
                          showSecret ? 'Hide bearer token' : 'Show bearer token'
                        }
                        className="absolute top-1/2 right-2 -translate-y-1/2 cursor-pointer rounded p-1 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
                      >
                        {showSecret ? (
                          <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
                        ) : (
                          <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                        )}
                      </button>
                    </div>
                    <FieldError
                      id="test-rest-bearer-error"
                      message={errors.bearerToken}
                    />
                  </div>
                )}
              </div>
            )}

            {isFileConnector && (
              <div className="space-y-3">
                {values.fileName && (
                  <div className="rounded-md border border-zinc-200/80 bg-zinc-50/70 px-3 py-2 text-[11px] text-zinc-600 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-300">
                    Configured filename:{' '}
                    <span className="font-mono font-medium text-zinc-900 dark:text-zinc-100">
                      {values.fileName}
                    </span>
                  </div>
                )}

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
                      errors.file
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
                            : `Max 256 KB · Read only when you click Run Connection Test`}
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
                    aria-invalid={Boolean(errors.file)}
                    aria-describedby={
                      errors.file ? 'test-conn-file-error' : undefined
                    }
                    className="sr-only"
                  />
                  <FieldError
                    id="test-conn-file-error"
                    message={errors.file}
                  />
                </div>
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
