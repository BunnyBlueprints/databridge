import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { AlertCircle, Loader2, X } from 'lucide-react'
import { ConnectionApiClientError } from '../../services/connectionService.ts'
import type {
  Connection,
  CreateConnectionPayload,
  UpdateConnectionPayload,
} from '../../types/connection.ts'
import type { ConnectorType } from '../../types/dashboard.ts'
import { Button } from '../ui/Button.tsx'
import { ConnectorIcon } from '../ui/ConnectorIcon.tsx'
import { ConnectorConfigFields } from './ConnectorConfigFields.tsx'
import {
  CONNECTOR_TYPES,
  buildCreateConnectionPayload,
  buildUpdateConnectionPayload,
  createFormValuesFromConnection,
  createInitialFormValues,
  getDefaultPort,
  stripSensitiveFormValues,
  validateConnectorForm,
} from './connectorForm.ts'
import type {
  ConnectionFormMode,
  ConnectorFormValues,
  FormErrors,
} from './connectorForm.ts'

export type AddConnectionModalProps =
  | {
      isOpen: boolean
      mode?: 'create'
      initialConnection?: undefined
      onClose: () => void
      onCreateConnection: (payload: CreateConnectionPayload) => Promise<void>
      onUpdateConnection?: (
        connectionId: string,
        payload: UpdateConnectionPayload,
      ) => Promise<void>
    }
  | {
      isOpen: boolean
      mode: 'edit'
      initialConnection: Connection | null
      onClose: () => void
      onCreateConnection?: (payload: CreateConnectionPayload) => Promise<void>
      onUpdateConnection: (
        connectionId: string,
        payload: UpdateConnectionPayload,
      ) => Promise<void>
    }

export function AddConnectionModal({
  isOpen,
  mode = 'create',
  initialConnection = null,
  onClose,
  onCreateConnection,
  onUpdateConnection,
}: AddConnectionModalProps) {
  if (!isOpen) return null
  if (mode === 'edit' && !initialConnection) return null

  const dialogKey =
    mode === 'edit' && initialConnection
      ? `edit-${initialConnection.id}`
      : 'create'

  return (
    <ConnectionFormDialog
      key={dialogKey}
      mode={mode}
      initialConnection={initialConnection}
      onClose={onClose}
      onCreateConnection={onCreateConnection}
      onUpdateConnection={onUpdateConnection}
    />
  )
}

function ConnectionFormDialog({
  mode,
  initialConnection,
  onClose,
  onCreateConnection,
  onUpdateConnection,
}: {
  mode: ConnectionFormMode
  initialConnection: Connection | null
  onClose: () => void
  onCreateConnection?: (payload: CreateConnectionPayload) => Promise<void>
  onUpdateConnection?: (
    connectionId: string,
    payload: UpdateConnectionPayload,
  ) => Promise<void>
}) {
  const isEdit = mode === 'edit' && initialConnection !== null

  const [values, setValues] = useState<ConnectorFormValues>(() =>
    isEdit
      ? createFormValuesFromConnection(initialConnection)
      : createInitialFormValues('PostgreSQL'),
  )
  const [errors, setErrors] = useState<FormErrors>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const nameInputRef = useRef<HTMLInputElement>(null)
  const isSubmittingRef = useRef(false)
  const isMountedRef = useRef(true)

  const handleRequestClose = useCallback(() => {
    if (isSubmittingRef.current) return
    setValues((prev) => stripSensitiveFormValues(prev))
    setSubmitError(null)
    onClose()
  }, [onClose])

  useEffect(() => {
    isMountedRef.current = true
    nameInputRef.current?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        handleRequestClose()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      isMountedRef.current = false
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [handleRequestClose])

  const handleFieldChange = <K extends keyof ConnectorFormValues>(
    field: K,
    value: ConnectorFormValues[K],
  ) => {
    setValues((prev) => ({ ...prev, [field]: value }))
    setSubmitError(null)
    setErrors((prev) => {
      if (!prev[field]) return prev
      const next = { ...prev }
      delete next[field]
      return next
    })
  }

  const handleConnectorTypeChange = (nextType: ConnectorType) => {
    if (isEdit || isSubmitting) return

    setValues((prev) => ({
      ...createInitialFormValues(nextType),
      name: prev.name,
      description: prev.description,
      port: getDefaultPort(nextType),
    }))
    setSubmitError(null)
    setErrors((prev) => (prev.name ? { name: prev.name } : {}))
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    if (isSubmittingRef.current) {
      return
    }

    const validationErrors = validateConnectorForm(values, {
      mode,
      existingConnection: initialConnection,
    })
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors)
      return
    }

    setSubmitError(null)
    setIsSubmitting(true)
    isSubmittingRef.current = true

    try {
      if (isEdit) {
        const payload = buildUpdateConnectionPayload(values, initialConnection)
        await onUpdateConnection?.(initialConnection.id, payload)
      } else {
        const payload = buildCreateConnectionPayload(values)
        await onCreateConnection?.(payload)
      }

      if (!isMountedRef.current) return
      setValues((prev) => stripSensitiveFormValues(prev))
      isSubmittingRef.current = false
      setIsSubmitting(false)
      onClose()
    } catch (error) {
      if (!isMountedRef.current) return
      isSubmittingRef.current = false
      setIsSubmitting(false)
      if (error instanceof ConnectionApiClientError) {
        setSubmitError(error.message)
      } else {
        setSubmitError(
          'Unable to save this connection. Verify the configuration and try again.',
        )
      }
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-connection-modal-title"
      aria-describedby="add-connection-modal-description"
    >
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-zinc-950/40 transition-opacity dark:bg-zinc-950/70"
        onClick={handleRequestClose}
      />

      {/* Modal panel */}
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative z-10 flex max-h-[90vh] w-full max-w-xl flex-col overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-900"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-zinc-200/80 px-5 py-4 dark:border-zinc-800">
          <div>
            <h2
              id="add-connection-modal-title"
              className="text-base font-semibold tracking-tight text-zinc-950 dark:text-zinc-50"
            >
              {isEdit ? 'Edit Connection' : 'Add Connection'}
            </h2>
            <p
              id="add-connection-modal-description"
              className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400"
            >
              {isEdit
                ? `Update configuration for ${initialConnection.name}. Stored credentials remain encrypted on the backend.`
                : 'Configure a persistent connector. Credentials are encrypted by the backend and never saved in the browser.'}
            </p>
          </div>
          <button
            type="button"
            disabled={isSubmitting}
            onClick={handleRequestClose}
            aria-label={
              isEdit
                ? 'Close Edit Connection modal'
                : 'Close Add Connection modal'
            }
            className="inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Scrollable form body */}
        <form
          onSubmit={handleSubmit}
          noValidate
          className="flex min-h-0 flex-1 flex-col"
        >
          <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
            {submitError && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-md border border-rose-200/90 bg-rose-50/80 px-3 py-2.5 text-xs text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-200"
              >
                <AlertCircle
                  className="mt-0.5 h-4 w-4 shrink-0 text-rose-600 dark:text-rose-400"
                  aria-hidden="true"
                />
                <span>{submitError}</span>
              </div>
            )}

            {/* Common fields */}
            <div className="space-y-3">
              <div>
                <label
                  htmlFor="conn-name"
                  className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Connection Name
                  <span className="ml-0.5 text-rose-600 dark:text-rose-400">
                    *
                  </span>
                </label>
                <input
                  ref={nameInputRef}
                  id="conn-name"
                  type="text"
                  disabled={isSubmitting}
                  value={values.name}
                  onChange={(e) => handleFieldChange('name', e.target.value)}
                  placeholder="prod-analytics-pg"
                  aria-invalid={Boolean(errors.name)}
                  aria-describedby={errors.name ? 'conn-name-error' : undefined}
                  className={`h-9 w-full rounded-md border bg-white px-3 text-xs text-zinc-900 shadow-[0_1px_2px_rgba(15,23,42,0.03)] placeholder:text-zinc-400 focus:ring-1 focus:outline-none disabled:cursor-not-allowed disabled:bg-zinc-50 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 ${
                    errors.name
                      ? 'border-rose-400 focus:border-rose-500 focus:ring-rose-500 dark:border-rose-700'
                      : 'border-zinc-200 focus:border-emerald-600 focus:ring-emerald-600 dark:border-zinc-700 dark:focus:border-emerald-500'
                  }`}
                />
                {errors.name && (
                  <p
                    id="conn-name-error"
                    className="mt-1 text-[11px] text-rose-600 dark:text-rose-400"
                  >
                    {errors.name}
                  </p>
                )}
              </div>

              <div>
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <span className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
                    Connector Type
                    {!isEdit && (
                      <span className="ml-0.5 text-rose-600 dark:text-rose-400">
                        *
                      </span>
                    )}
                  </span>
                  {isEdit && (
                    <span className="text-[11px] text-zinc-400 dark:text-zinc-500">
                      Type locked while editing
                    </span>
                  )}
                </div>
                <div
                  className="grid grid-cols-2 gap-2 sm:grid-cols-3"
                  role="group"
                  aria-label="Connector type"
                >
                  {CONNECTOR_TYPES.map((type) => {
                    const selected = values.type === type
                    return (
                      <button
                        key={type}
                        type="button"
                        disabled={isSubmitting || (isEdit && !selected)}
                        onClick={() => handleConnectorTypeChange(type)}
                        aria-pressed={selected}
                        className={`flex items-center gap-2 rounded-md border px-2.5 py-2 text-left text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none ${
                          isEdit && !selected
                            ? 'cursor-not-allowed border-zinc-200/70 bg-zinc-50/70 text-zinc-400 opacity-60 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-600'
                            : selected
                              ? 'cursor-pointer border-emerald-600 bg-emerald-50/70 text-emerald-900 dark:border-emerald-500 dark:bg-emerald-950/50 dark:text-emerald-200'
                              : 'cursor-pointer border-zinc-200 bg-white text-zinc-700 hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300 dark:hover:border-zinc-600'
                        }`}
                      >
                        <ConnectorIcon type={type} size="sm" />
                        <span className="truncate">{type}</span>
                      </button>
                    )
                  })}
                </div>
              </div>

              <div>
                <label
                  htmlFor="conn-description"
                  className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Description{' '}
                  <span className="font-normal text-zinc-400">(optional)</span>
                </label>
                <textarea
                  id="conn-description"
                  rows={2}
                  disabled={isSubmitting}
                  value={values.description}
                  onChange={(e) =>
                    handleFieldChange('description', e.target.value)
                  }
                  placeholder="Optional summary of what data this connector provides"
                  className="w-full resize-none rounded-md border border-zinc-200 bg-white px-3 py-2 text-xs text-zinc-900 shadow-[0_1px_2px_rgba(15,23,42,0.03)] placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none disabled:cursor-not-allowed disabled:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
                />
              </div>
            </div>

            {/* Connector-specific configuration */}
            <div className="border-t border-zinc-200/80 pt-4 dark:border-zinc-800">
              <div className="mb-3 text-[11px] font-semibold tracking-wider text-zinc-400 uppercase">
                {values.type} Configuration
              </div>
              <ConnectorConfigFields
                values={values}
                errors={errors}
                mode={mode}
                existingConnection={initialConnection}
                disabled={isSubmitting}
                onFieldChange={handleFieldChange}
              />
            </div>
          </div>

          {/* Footer */}
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-200/80 bg-zinc-50/70 px-5 py-3 dark:border-zinc-800 dark:bg-zinc-950/60">
            <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
              {isEdit ? (
                <>
                  Metadata edits keep status{' '}
                  <strong className="font-medium text-zinc-700 dark:text-zinc-200">
                    {initialConnection.status}
                  </strong>
                  ; config or credential changes reset status to Disconnected
                </>
              ) : (
                <>
                  Initial status will be set to{' '}
                  <strong className="font-medium text-zinc-700 dark:text-zinc-200">
                    Disconnected
                  </strong>
                </>
              )}
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={isSubmitting}
                onClick={handleRequestClose}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                type="submit"
                disabled={isSubmitting}
              >
                {isSubmitting ? (
                  <>
                    <Loader2
                      className="h-3.5 w-3.5 animate-spin"
                      aria-hidden="true"
                    />
                    <span>{isEdit ? 'Saving…' : 'Creating…'}</span>
                  </>
                ) : (
                  <span>{isEdit ? 'Save Changes' : 'Save Connection'}</span>
                )}
              </Button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
