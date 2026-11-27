import { useState } from 'react'
import { Eye, EyeOff, FileText, Upload } from 'lucide-react'
import type { ChangeEvent } from 'react'
import type { Connection } from '../../types/connection.ts'
import type {
  ConnectionFormMode,
  ConnectorFormValues,
  FormErrors,
  RestAuthType,
} from './connectorForm.ts'

interface ConnectorConfigFieldsProps {
  values: ConnectorFormValues
  errors: FormErrors
  mode?: ConnectionFormMode
  existingConnection?: Connection | null
  disabled?: boolean
  onFieldChange: <K extends keyof ConnectorFormValues>(
    field: K,
    value: ConnectorFormValues[K],
  ) => void
}

const INPUT_BASE =
  'h-9 w-full rounded-md border bg-white px-3 text-xs text-zinc-900 shadow-[0_1px_2px_rgba(15,23,42,0.03)] placeholder:text-zinc-400 focus:outline-none focus:ring-1 disabled:cursor-not-allowed disabled:bg-zinc-50 disabled:text-zinc-500 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500'

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

function FieldHint({ children }: { children: string }) {
  return (
    <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">
      {children}
    </p>
  )
}

function FieldLabel({
  htmlFor,
  label,
  required = false,
}: {
  htmlFor: string
  label: string
  required?: boolean
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
    >
      {label}
      {required && (
        <span className="ml-0.5 text-rose-600 dark:text-rose-400">*</span>
      )}
    </label>
  )
}

function SqlDatabaseFields({
  values,
  errors,
  mode = 'create',
  disabled = false,
  onFieldChange,
}: ConnectorConfigFieldsProps) {
  const [showPassword, setShowPassword] = useState(false)
  const isEdit = mode === 'edit'

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="sm:col-span-2">
          <FieldLabel htmlFor="conn-host" label="Host" required />
          <input
            id="conn-host"
            type="text"
            disabled={disabled}
            value={values.host}
            onChange={(e) => onFieldChange('host', e.target.value)}
            placeholder="db.internal.example.com"
            aria-invalid={Boolean(errors.host)}
            aria-describedby={errors.host ? 'conn-host-error' : undefined}
            className={`${INPUT_BASE} ${getInputBorder(Boolean(errors.host))}`}
          />
          <FieldError id="conn-host-error" message={errors.host} />
        </div>

        <div>
          <FieldLabel htmlFor="conn-port" label="Port" required />
          <input
            id="conn-port"
            type="text"
            inputMode="numeric"
            disabled={disabled}
            value={values.port}
            onChange={(e) => onFieldChange('port', e.target.value)}
            placeholder={values.type === 'MySQL' ? '3306' : '5432'}
            aria-invalid={Boolean(errors.port)}
            aria-describedby={errors.port ? 'conn-port-error' : undefined}
            className={`${INPUT_BASE} tabular-nums ${getInputBorder(Boolean(errors.port))}`}
          />
          <FieldError id="conn-port-error" message={errors.port} />
        </div>
      </div>

      <div>
        <FieldLabel htmlFor="conn-database" label="Database Name" required />
        <input
          id="conn-database"
          type="text"
          disabled={disabled}
          value={values.database}
          onChange={(e) => onFieldChange('database', e.target.value)}
          placeholder="orders_prod"
          aria-invalid={Boolean(errors.database)}
          aria-describedby={
            errors.database ? 'conn-database-error' : undefined
          }
          className={`${INPUT_BASE} ${getInputBorder(Boolean(errors.database))}`}
        />
        <FieldError id="conn-database-error" message={errors.database} />
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <FieldLabel
            htmlFor="conn-username"
            label={isEdit ? 'Replace Username (optional)' : 'Username'}
            required={!isEdit}
          />
          <input
            id="conn-username"
            type="text"
            autoComplete="off"
            disabled={disabled}
            value={values.username}
            onChange={(e) => onFieldChange('username', e.target.value)}
            placeholder={
              isEdit ? 'Leave blank to keep existing' : 'databridge_ro'
            }
            aria-invalid={Boolean(errors.username)}
            aria-describedby={
              errors.username ? 'conn-username-error' : undefined
            }
            className={`${INPUT_BASE} ${getInputBorder(Boolean(errors.username))}`}
          />
          <FieldError id="conn-username-error" message={errors.username} />
        </div>

        <div>
          <FieldLabel
            htmlFor="conn-password"
            label={
              isEdit ? 'Replace Password (optional)' : 'Password (optional)'
            }
          />
          <div className="relative">
            <input
              id="conn-password"
              type={showPassword ? 'text' : 'password'}
              autoComplete="new-password"
              disabled={disabled}
              value={values.password}
              onChange={(e) => onFieldChange('password', e.target.value)}
              placeholder={
                isEdit ? 'Leave blank to keep existing' : '••••••••••••'
              }
              aria-invalid={Boolean(errors.password)}
              aria-describedby={
                errors.password ? 'conn-password-error' : undefined
              }
              className={`${INPUT_BASE} pr-9 ${getInputBorder(Boolean(errors.password))}`}
            />
            <button
              type="button"
              disabled={disabled}
              onClick={() => setShowPassword((prev) => !prev)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              className="absolute top-1/2 right-2 -translate-y-1/2 cursor-pointer rounded p-1 text-zinc-400 hover:text-zinc-700 disabled:cursor-not-allowed dark:hover:text-zinc-200"
            >
              {showPassword ? (
                <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <Eye className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>
          </div>
          <FieldError id="conn-password-error" message={errors.password} />
        </div>
      </div>

      <FieldHint>
        {isEdit
          ? 'Encrypted username and password stay hidden. Leave both fields empty to keep existing credentials; replacing SQL credentials requires entering Username.'
          : 'Username and optional password are encrypted in the backend vault and never stored in plain configuration.'}
      </FieldHint>
    </div>
  )
}

function MongoDbFields({
  values,
  errors,
  mode = 'create',
  existingConnection,
  disabled = false,
  onFieldChange,
}: ConnectorConfigFieldsProps) {
  const [showPassword, setShowPassword] = useState(false)
  const isEdit = mode === 'edit'
  const canClearExisting =
    isEdit && existingConnection?.details?.authConfigured === true

  return (
    <div className="space-y-3">
      <div>
        <FieldLabel htmlFor="conn-mongo-uri" label="Connection URI" required />
        <input
          id="conn-mongo-uri"
          type="text"
          autoComplete="off"
          disabled={disabled}
          value={values.mongoUri}
          onChange={(e) => onFieldChange('mongoUri', e.target.value)}
          placeholder="mongodb+srv://cluster01.mongodb.net"
          aria-invalid={Boolean(errors.mongoUri)}
          aria-describedby={
            errors.mongoUri ? 'conn-mongo-uri-error' : undefined
          }
          className={`${INPUT_BASE} font-mono ${getInputBorder(Boolean(errors.mongoUri))}`}
        />
        <FieldError id="conn-mongo-uri-error" message={errors.mongoUri} />
        <FieldHint>
          Credential-free URI only (do not include user:password@ in the URI).
        </FieldHint>
      </div>

      <div>
        <FieldLabel
          htmlFor="conn-mongo-db"
          label="Database Name"
          required
        />
        <input
          id="conn-mongo-db"
          type="text"
          disabled={disabled}
          value={values.mongoDatabase}
          onChange={(e) => onFieldChange('mongoDatabase', e.target.value)}
          placeholder="telemetry"
          aria-invalid={Boolean(errors.mongoDatabase)}
          aria-describedby={
            errors.mongoDatabase ? 'conn-mongo-db-error' : undefined
          }
          className={`${INPUT_BASE} ${getInputBorder(Boolean(errors.mongoDatabase))}`}
        />
        <FieldError
          id="conn-mongo-db-error"
          message={errors.mongoDatabase}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <FieldLabel
            htmlFor="conn-mongo-user"
            label={isEdit ? 'Replace Username (optional)' : 'Username (optional)'}
          />
          <input
            id="conn-mongo-user"
            type="text"
            autoComplete="off"
            disabled={disabled || values.clearCredentials}
            value={values.mongoUsername}
            onChange={(e) => onFieldChange('mongoUsername', e.target.value)}
            placeholder={
              isEdit ? 'Leave blank to keep existing' : 'databridge_reader'
            }
            className={`${INPUT_BASE} ${getInputBorder(false)}`}
          />
        </div>

        <div>
          <FieldLabel
            htmlFor="conn-mongo-password"
            label={isEdit ? 'Replace Password (optional)' : 'Password (optional)'}
          />
          <div className="relative">
            <input
              id="conn-mongo-password"
              type={showPassword ? 'text' : 'password'}
              autoComplete="new-password"
              disabled={disabled || values.clearCredentials}
              value={values.mongoPassword}
              onChange={(e) => onFieldChange('mongoPassword', e.target.value)}
              placeholder={
                isEdit ? 'Leave blank to keep existing' : '••••••••••••'
              }
              className={`${INPUT_BASE} pr-9 ${getInputBorder(false)}`}
            />
            <button
              type="button"
              disabled={disabled || values.clearCredentials}
              onClick={() => setShowPassword((prev) => !prev)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              className="absolute top-1/2 right-2 -translate-y-1/2 cursor-pointer rounded p-1 text-zinc-400 hover:text-zinc-700 disabled:cursor-not-allowed dark:hover:text-zinc-200"
            >
              {showPassword ? (
                <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <Eye className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>
          </div>
        </div>
      </div>

      {isEdit && (
        <FieldHint>
          Leave credential fields empty to keep existing stored credentials intact.
        </FieldHint>
      )}

      {canClearExisting && (
        <label className="flex cursor-pointer items-center gap-2 rounded-md border border-zinc-200/80 bg-zinc-50/70 px-3 py-2 text-xs text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-300">
          <input
            type="checkbox"
            disabled={disabled}
            checked={values.clearCredentials}
            onChange={(e) => {
              const checked = e.target.checked
              onFieldChange('clearCredentials', checked)
              if (checked) {
                onFieldChange('mongoUsername', '')
                onFieldChange('mongoPassword', '')
              }
            }}
            className="h-3.5 w-3.5 rounded border-zinc-300 text-emerald-600 focus:ring-emerald-600"
          />
          <span>Clear stored credentials on save (switch to passwordless)</span>
        </label>
      )}
    </div>
  )
}

function RestApiFields({
  values,
  errors,
  mode = 'create',
  existingConnection,
  disabled = false,
  onFieldChange,
}: ConnectorConfigFieldsProps) {
  const [showSecret, setShowSecret] = useState(false)
  const isEdit = mode === 'edit'

  const keepingExistingApiKey =
    isEdit &&
    existingConnection?.details?.authType === 'api_key' &&
    existingConnection.details.authConfigured === true
  const keepingExistingBearer =
    isEdit &&
    existingConnection?.details?.authType === 'bearer' &&
    existingConnection.details.authConfigured === true

  return (
    <div className="space-y-3">
      <div>
        <FieldLabel htmlFor="conn-rest-url" label="Base URL" required />
        <input
          id="conn-rest-url"
          type="url"
          disabled={disabled}
          value={values.baseUrl}
          onChange={(e) => onFieldChange('baseUrl', e.target.value)}
          placeholder="https://api.example.com/v2"
          aria-invalid={Boolean(errors.baseUrl)}
          aria-describedby={errors.baseUrl ? 'conn-rest-url-error' : undefined}
          className={`${INPUT_BASE} font-mono ${getInputBorder(Boolean(errors.baseUrl))}`}
        />
        <FieldError id="conn-rest-url-error" message={errors.baseUrl} />
      </div>

      <div>
        <FieldLabel htmlFor="conn-rest-auth" label="Authentication Type" />
        <select
          id="conn-rest-auth"
          disabled={disabled}
          value={values.authType}
          onChange={(e) => {
            const nextType = e.target.value as RestAuthType
            onFieldChange('authType', nextType)
            onFieldChange('apiKey', '')
            onFieldChange('bearerToken', '')
          }}
          className={`${INPUT_BASE} ${getInputBorder(false)}`}
        >
          <option value="none">None</option>
          <option value="api_key">API Key</option>
          <option value="bearer">Bearer Token</option>
        </select>
        {isEdit &&
          values.authType === 'none' &&
          existingConnection?.details?.authConfigured === true && (
            <FieldHint>
              Saving with None authentication clears previously stored credentials.
            </FieldHint>
          )}
      </div>

      {values.authType === 'api_key' && (
        <div>
          <FieldLabel
            htmlFor="conn-api-key"
            label={
              keepingExistingApiKey ? 'Replace API Key (optional)' : 'API Key'
            }
            required={!keepingExistingApiKey}
          />
          <div className="relative">
            <input
              id="conn-api-key"
              type={showSecret ? 'text' : 'password'}
              autoComplete="off"
              disabled={disabled}
              value={values.apiKey}
              onChange={(e) => onFieldChange('apiKey', e.target.value)}
              placeholder={
                keepingExistingApiKey
                  ? 'Leave blank to keep existing API key'
                  : 'db_live_••••••••••••'
              }
              aria-invalid={Boolean(errors.apiKey)}
              aria-describedby={
                errors.apiKey ? 'conn-api-key-error' : undefined
              }
              className={`${INPUT_BASE} pr-9 font-mono ${getInputBorder(Boolean(errors.apiKey))}`}
            />
            <button
              type="button"
              disabled={disabled}
              onClick={() => setShowSecret((prev) => !prev)}
              aria-label={showSecret ? 'Hide API key' : 'Show API key'}
              className="absolute top-1/2 right-2 -translate-y-1/2 cursor-pointer rounded p-1 text-zinc-400 hover:text-zinc-700 disabled:cursor-not-allowed dark:hover:text-zinc-200"
            >
              {showSecret ? (
                <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <Eye className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>
          </div>
          <FieldError id="conn-api-key-error" message={errors.apiKey} />
          {keepingExistingApiKey && !errors.apiKey && (
            <FieldHint>
              Existing API key is hidden and preserved unless replaced.
            </FieldHint>
          )}
        </div>
      )}

      {values.authType === 'bearer' && (
        <div>
          <FieldLabel
            htmlFor="conn-bearer-token"
            label={
              keepingExistingBearer
                ? 'Replace Bearer Token (optional)'
                : 'Bearer Token'
            }
            required={!keepingExistingBearer}
          />
          <div className="relative">
            <input
              id="conn-bearer-token"
              type={showSecret ? 'text' : 'password'}
              autoComplete="off"
              disabled={disabled}
              value={values.bearerToken}
              onChange={(e) => onFieldChange('bearerToken', e.target.value)}
              placeholder={
                keepingExistingBearer
                  ? 'Leave blank to keep existing bearer token'
                  : 'eyJhbGciOiJIUzI1NiIs••••••'
              }
              aria-invalid={Boolean(errors.bearerToken)}
              aria-describedby={
                errors.bearerToken ? 'conn-bearer-error' : undefined
              }
              className={`${INPUT_BASE} pr-9 font-mono ${getInputBorder(Boolean(errors.bearerToken))}`}
            />
            <button
              type="button"
              disabled={disabled}
              onClick={() => setShowSecret((prev) => !prev)}
              aria-label={
                showSecret ? 'Hide bearer token' : 'Show bearer token'
              }
              className="absolute top-1/2 right-2 -translate-y-1/2 cursor-pointer rounded p-1 text-zinc-400 hover:text-zinc-700 disabled:cursor-not-allowed dark:hover:text-zinc-200"
            >
              {showSecret ? (
                <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <Eye className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>
          </div>
          <FieldError id="conn-bearer-error" message={errors.bearerToken} />
          {keepingExistingBearer && !errors.bearerToken && (
            <FieldHint>
              Existing bearer token is hidden and preserved unless replaced.
            </FieldHint>
          )}
        </div>
      )}
    </div>
  )
}

function FileConnectorFields({
  values,
  errors,
  disabled = false,
  onFieldChange,
}: ConnectorConfigFieldsProps) {
  const accept =
    values.type === 'CSV' ? '.csv,text/csv' : '.json,application/json'
  const extLabel = values.type === 'CSV' ? '.csv' : '.json'

  const handleFileSelect = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    onFieldChange('fileName', file ? file.name : '')
  }

  return (
    <div className="space-y-3">
      <div>
        <span className="mb-1 block text-xs font-medium text-zinc-700 dark:text-zinc-300">
          {values.type} Source File
          <span className="ml-0.5 text-rose-600 dark:text-rose-400">*</span>
        </span>
        <label
          htmlFor="conn-file-input"
          className={`flex items-center justify-between gap-3 rounded-md border border-dashed px-3.5 py-3 transition-colors ${
            disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'
          } ${
            errors.fileName
              ? 'border-rose-400 bg-rose-50/40 dark:border-rose-700 dark:bg-rose-950/20'
              : 'border-zinc-300 bg-zinc-50/60 hover:border-emerald-500 hover:bg-emerald-50/30 dark:border-zinc-700 dark:bg-zinc-950/60 dark:hover:border-emerald-600'
          }`}
        >
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-zinc-200 bg-white text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
              {values.fileName ? (
                <FileText className="h-4 w-4 text-emerald-600" aria-hidden="true" />
              ) : (
                <Upload className="h-4 w-4" aria-hidden="true" />
              )}
            </span>
            <div className="min-w-0">
              <div className="truncate text-xs font-medium text-zinc-800 dark:text-zinc-200">
                {values.fileName || `Choose a ${extLabel} file…`}
              </div>
              <div className="text-[11px] text-zinc-500 dark:text-zinc-400">
                {values.fileName
                  ? 'Only filename metadata is saved; file content is transient on test'
                  : `Accepted format: ${extLabel}`}
              </div>
            </div>
          </div>
          <span className="shrink-0 rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200">
            Browse
          </span>
        </label>
        <input
          id="conn-file-input"
          type="file"
          disabled={disabled}
          accept={accept}
          onChange={handleFileSelect}
          aria-invalid={Boolean(errors.fileName)}
          aria-describedby={errors.fileName ? 'conn-file-error' : undefined}
          className="sr-only"
        />
        <FieldError id="conn-file-error" message={errors.fileName} />
      </div>
    </div>
  )
}

export function ConnectorConfigFields(props: ConnectorConfigFieldsProps) {
  switch (props.values.type) {
    case 'PostgreSQL':
    case 'MySQL':
      return <SqlDatabaseFields {...props} />
    case 'MongoDB':
      return <MongoDbFields {...props} />
    case 'REST API':
      return <RestApiFields {...props} />
    case 'CSV':
    case 'JSON':
      return <FileConnectorFields {...props} />
  }
}
