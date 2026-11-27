import { useRef } from 'react'
import { FileText, Loader2, Plus, Radar, Trash2, Upload } from 'lucide-react'
import type { Connection } from '../../types/connection.ts'
import type {
  CanonicalFieldType,
  RestDiscoveryMode,
  SchemaObjectType,
} from '../../types/schema.ts'
import { Button } from '../ui/Button.tsx'
import { Card, CardContent } from '../ui/Card.tsx'
import {
  CANONICAL_FIELD_TYPES,
  createEmptyManualField,
  type DiscoveryFormValidationResult,
  type DiscoveryFormValues,
  type ManualFieldDraft,
} from './discoveryForm.ts'

interface DiscoveryOptionsPanelProps {
  connection: Connection
  values: DiscoveryFormValues
  errors: DiscoveryFormValidationResult['errors']
  selectedFile: File | null
  isDiscovering: boolean
  hasExistingSchema: boolean
  onChange: (updater: (prev: DiscoveryFormValues) => DiscoveryFormValues) => void
  onSelectFile: (file: File | null) => void
  onDiscover: () => void
}

const REST_MODE_TABS: Array<{ mode: RestDiscoveryMode; label: string }> = [
  { mode: 'sample_get', label: 'GET Sample' },
  { mode: 'openapi', label: 'OpenAPI JSON' },
  { mode: 'manual', label: 'Manual Schema' },
]

const MANUAL_OBJECT_TYPES: SchemaObjectType[] = [
  'endpoint',
  'record_set',
  'table',
  'collection',
  'file',
  'view',
]

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KB`
}

export function DiscoveryOptionsPanel({
  connection,
  values,
  errors,
  selectedFile,
  isDiscovering,
  hasExistingSchema,
  onChange,
  onSelectFile,
  onDiscover,
}: DiscoveryOptionsPanelProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const connType = connection.type

  const updateManualField = (
    index: number,
    patch: Partial<ManualFieldDraft>,
  ) => {
    onChange((prev) => ({
      ...prev,
      manualFields: prev.manualFields.map((fld, idx) =>
        idx === index ? { ...fld, ...patch } : fld,
      ),
    }))
  }

  return (
    <Card className="dark:border-zinc-800 dark:bg-zinc-900">
      <CardContent className="space-y-4 px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              Discovery Configuration
            </h2>
            <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
              {connType === 'PostgreSQL' || connType === 'MySQL'
                ? 'Executes read-only catalog queries against information_schema.'
                : connType === 'MongoDB'
                  ? 'Lists collections and infers nested fields from bounded read-only samples.'
                  : connType === 'CSV' || connType === 'JSON'
                    ? 'Infers schema from a transient local file upload without storing file contents.'
                    : 'Inspects REST endpoints via bounded GET sampling, OpenAPI JSON, or manual schema rules.'}
            </p>
          </div>

          <Button
            variant="primary"
            size="sm"
            disabled={isDiscovering}
            onClick={onDiscover}
            aria-busy={isDiscovering}
          >
            {isDiscovering ? (
              <>
                <Loader2
                  className="h-3.5 w-3.5 animate-spin"
                  aria-hidden="true"
                />
                <span>Discovering…</span>
              </>
            ) : (
              <>
                <Radar className="h-3.5 w-3.5" aria-hidden="true" />
                <span>
                  {hasExistingSchema ? 'Re-discover Schema' : 'Discover Schema'}
                </span>
              </>
            )}
          </Button>
        </div>

        {/* PostgreSQL / MySQL options */}
        {(connType === 'PostgreSQL' || connType === 'MySQL') && (
          <div className="grid grid-cols-1 gap-3 border-t border-zinc-100 pt-3 sm:grid-cols-2 dark:border-zinc-800">
            {connType === 'PostgreSQL' && (
              <div>
                <label
                  htmlFor="disc-include-schemas"
                  className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Filter schemas (optional)
                </label>
                <input
                  id="disc-include-schemas"
                  type="text"
                  disabled={isDiscovering}
                  value={values.includeSchemasText}
                  onChange={(e) =>
                    onChange((prev) => ({
                      ...prev,
                      includeSchemasText: e.target.value,
                    }))
                  }
                  placeholder="public, analytics"
                  className="mt-1 h-8 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                />
                {errors.includeSchemasText && (
                  <p className="mt-1 text-[11px] text-rose-600">
                    {errors.includeSchemasText}
                  </p>
                )}
              </div>
            )}

            <div className="flex items-end pb-1">
              <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-zinc-700 dark:text-zinc-300">
                <input
                  type="checkbox"
                  disabled={isDiscovering}
                  checked={values.includeViews}
                  onChange={(e) =>
                    onChange((prev) => ({
                      ...prev,
                      includeViews: e.target.checked,
                    }))
                  }
                  className="h-4 w-4 rounded border-zinc-300 text-emerald-600 focus:ring-emerald-600"
                />
                <span>Include database views alongside tables</span>
              </label>
            </div>
          </div>
        )}

        {/* MongoDB options */}
        {connType === 'MongoDB' && (
          <div className="grid grid-cols-1 gap-3 border-t border-zinc-100 pt-3 sm:grid-cols-2 dark:border-zinc-800">
            <div>
              <label
                htmlFor="disc-mongo-sample"
                className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
              >
                Sample documents per collection
              </label>
              <input
                id="disc-mongo-sample"
                type="number"
                min={1}
                max={1000}
                disabled={isDiscovering}
                value={values.sampleLimit}
                onChange={(e) =>
                  onChange((prev) => ({
                    ...prev,
                    sampleLimit: e.target.value,
                  }))
                }
                className="mt-1 h-8 w-44 rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 tabular-nums focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
              />
              {errors.sampleLimit && (
                <p className="mt-1 text-[11px] text-rose-600">
                  {errors.sampleLimit}
                </p>
              )}
            </div>
          </div>
        )}

        {/* CSV / JSON transient upload options */}
        {(connType === 'CSV' || connType === 'JSON') && (
          <div className="space-y-3 border-t border-zinc-100 pt-3 dark:border-zinc-800">
            <div>
              <label
                htmlFor="disc-file-input"
                className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
              >
                {connType === 'CSV' ? 'CSV file' : 'JSON file'} (transient upload)
              </label>
              <label
                htmlFor="disc-file-input"
                className={`mt-1 flex cursor-pointer items-center justify-between gap-3 rounded-md border border-dashed px-3.5 py-2.5 transition-colors ${
                  errors.file
                    ? 'border-rose-300 bg-rose-50/40 dark:border-rose-800 dark:bg-rose-950/20'
                    : 'border-zinc-300 bg-zinc-50/60 hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-950/50'
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
                        : `Choose a ${connType === 'CSV' ? '.csv' : '.json'} file…`}
                    </div>
                    <div className="text-[11px] text-zinc-500 dark:text-zinc-400">
                      {selectedFile
                        ? `${formatFileSize(selectedFile.size)} · Read in memory on Discover only`
                        : 'Max 256 KB · File rows are never stored on the server'}
                    </div>
                  </div>
                </div>
                <span className="shrink-0 rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200">
                  Browse
                </span>
              </label>
              <input
                ref={fileInputRef}
                id="disc-file-input"
                type="file"
                disabled={isDiscovering}
                accept={
                  connType === 'CSV'
                    ? '.csv,text/csv'
                    : '.json,application/json'
                }
                onChange={(e) => onSelectFile(e.target.files?.[0] ?? null)}
                className="sr-only"
              />
              {errors.file && (
                <p className="mt-1 text-[11px] text-rose-600">{errors.file}</p>
              )}
            </div>

            {connType === 'JSON' && (
              <div>
                <label
                  htmlFor="disc-json-record-path"
                  className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Record envelope path (optional)
                </label>
                <input
                  id="disc-json-record-path"
                  type="text"
                  disabled={isDiscovering}
                  value={values.recordPath}
                  onChange={(e) =>
                    onChange((prev) => ({
                      ...prev,
                      recordPath: e.target.value,
                    }))
                  }
                  placeholder="e.g. data.items"
                  className="mt-1 h-8 w-full max-w-sm rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                />
                {errors.recordPath && (
                  <p className="mt-1 text-[11px] text-rose-600">
                    {errors.recordPath}
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {/* REST API options */}
        {connType === 'REST API' && (
          <div className="space-y-3 border-t border-zinc-100 pt-3 dark:border-zinc-800">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
                Discovery mode:
              </span>
              <div
                className="inline-flex rounded-md border border-zinc-200 bg-zinc-50 p-0.5 dark:border-zinc-800 dark:bg-zinc-950"
                role="tablist"
                aria-label="REST API discovery mode"
              >
                {REST_MODE_TABS.map((tab) => {
                  const active = values.restMode === tab.mode
                  return (
                    <button
                      key={tab.mode}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      disabled={isDiscovering}
                      onClick={() =>
                        onChange((prev) => ({ ...prev, restMode: tab.mode }))
                      }
                      className={`cursor-pointer rounded px-2.5 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none ${
                        active
                          ? 'bg-white text-zinc-900 shadow-xs dark:bg-zinc-800 dark:text-zinc-100'
                          : 'text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100'
                      }`}
                    >
                      {tab.label}
                    </button>
                  )
                })}
              </div>
            </div>

            {values.restMode === 'sample_get' && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <div>
                  <label
                    htmlFor="disc-rest-resource"
                    className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Relative resource path (optional)
                  </label>
                  <input
                    id="disc-rest-resource"
                    type="text"
                    disabled={isDiscovering}
                    value={values.resourcePath}
                    onChange={(e) =>
                      onChange((prev) => ({
                        ...prev,
                        resourcePath: e.target.value,
                      }))
                    }
                    placeholder="/v1/customers"
                    className="mt-1 h-8 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                  />
                  {errors.resourcePath && (
                    <p className="mt-1 text-[11px] text-rose-600">
                      {errors.resourcePath}
                    </p>
                  )}
                </div>

                <div>
                  <label
                    htmlFor="disc-rest-record-path"
                    className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Record path (optional)
                  </label>
                  <input
                    id="disc-rest-record-path"
                    type="text"
                    disabled={isDiscovering}
                    value={values.recordPath}
                    onChange={(e) =>
                      onChange((prev) => ({
                        ...prev,
                        recordPath: e.target.value,
                      }))
                    }
                    placeholder="data.items"
                    className="mt-1 h-8 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                  />
                  {errors.recordPath && (
                    <p className="mt-1 text-[11px] text-rose-600">
                      {errors.recordPath}
                    </p>
                  )}
                </div>

                <div>
                  <label
                    htmlFor="disc-rest-sample-limit"
                    className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                  >
                    Sample records
                  </label>
                  <input
                    id="disc-rest-sample-limit"
                    type="number"
                    min={1}
                    max={1000}
                    disabled={isDiscovering}
                    value={values.sampleLimit}
                    onChange={(e) =>
                      onChange((prev) => ({
                        ...prev,
                        sampleLimit: e.target.value,
                      }))
                    }
                    className="mt-1 h-8 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 tabular-nums focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                  />
                  {errors.sampleLimit && (
                    <p className="mt-1 text-[11px] text-rose-600">
                      {errors.sampleLimit}
                    </p>
                  )}
                </div>
              </div>
            )}

            {values.restMode === 'openapi' && (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-4 text-xs">
                  <label className="inline-flex cursor-pointer items-center gap-1.5 text-zinc-700 dark:text-zinc-300">
                    <input
                      type="radio"
                      name="openapi-source"
                      checked={values.openApiSource === 'path'}
                      onChange={() =>
                        onChange((prev) => ({
                          ...prev,
                          openApiSource: 'path',
                        }))
                      }
                    />
                    <span>Fetch spec from relative path</span>
                  </label>
                  <label className="inline-flex cursor-pointer items-center gap-1.5 text-zinc-700 dark:text-zinc-300">
                    <input
                      type="radio"
                      name="openapi-source"
                      checked={values.openApiSource === 'json'}
                      onChange={() =>
                        onChange((prev) => ({
                          ...prev,
                          openApiSource: 'json',
                        }))
                      }
                    />
                    <span>Paste OpenAPI JSON spec</span>
                  </label>
                </div>

                {values.openApiSource === 'path' ? (
                  <div>
                    <label
                      htmlFor="disc-openapi-path"
                      className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      OpenAPI JSON endpoint path
                    </label>
                    <input
                      id="disc-openapi-path"
                      type="text"
                      disabled={isDiscovering}
                      value={values.openApiPath}
                      onChange={(e) =>
                        onChange((prev) => ({
                          ...prev,
                          openApiPath: e.target.value,
                        }))
                      }
                      placeholder="/openapi.json"
                      className="mt-1 h-8 w-full max-w-md rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                    />
                    {errors.openApiPath && (
                      <p className="mt-1 text-[11px] text-rose-600">
                        {errors.openApiPath}
                      </p>
                    )}
                  </div>
                ) : (
                  <div>
                    <label
                      htmlFor="disc-openapi-json"
                      className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      OpenAPI 3.x / Swagger 2.0 JSON specification
                    </label>
                    <textarea
                      id="disc-openapi-json"
                      rows={4}
                      disabled={isDiscovering}
                      value={values.openApiJsonText}
                      onChange={(e) =>
                        onChange((prev) => ({
                          ...prev,
                          openApiJsonText: e.target.value,
                        }))
                      }
                      placeholder='{"openapi":"3.0.3","paths":{...}}'
                      className="mt-1 w-full rounded-md border border-zinc-200 bg-white p-2.5 font-mono text-[11px] text-zinc-900 placeholder:text-zinc-400 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                    />
                    {errors.openApiJsonText && (
                      <p className="mt-1 text-[11px] text-rose-600">
                        {errors.openApiJsonText}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}

            {values.restMode === 'manual' && (
              <div className="space-y-3">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div>
                    <label
                      htmlFor="disc-manual-key"
                      className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      Object key
                    </label>
                    <input
                      id="disc-manual-key"
                      type="text"
                      disabled={isDiscovering}
                      value={values.manualObjectKey}
                      onChange={(e) =>
                        onChange((prev) => ({
                          ...prev,
                          manualObjectKey: e.target.value,
                        }))
                      }
                      placeholder="POST /v1/leads"
                      className="mt-1 h-8 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                    />
                    {errors.manualObjectKey && (
                      <p className="mt-1 text-[11px] text-rose-600">
                        {errors.manualObjectKey}
                      </p>
                    )}
                  </div>

                  <div>
                    <label
                      htmlFor="disc-manual-name"
                      className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      Display name (optional)
                    </label>
                    <input
                      id="disc-manual-name"
                      type="text"
                      disabled={isDiscovering}
                      value={values.manualObjectName}
                      onChange={(e) =>
                        onChange((prev) => ({
                          ...prev,
                          manualObjectName: e.target.value,
                        }))
                      }
                      placeholder="leads"
                      className="mt-1 h-8 w-full rounded-md border border-zinc-200 bg-white px-2.5 text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                    />
                  </div>

                  <div>
                    <label
                      htmlFor="disc-manual-type"
                      className="block text-xs font-medium text-zinc-700 dark:text-zinc-300"
                    >
                      Object type
                    </label>
                    <select
                      id="disc-manual-type"
                      disabled={isDiscovering}
                      value={values.manualObjectType}
                      onChange={(e) =>
                        onChange((prev) => ({
                          ...prev,
                          manualObjectType: e.target.value as SchemaObjectType,
                        }))
                      }
                      className="mt-1 h-8 w-full rounded-md border border-zinc-200 bg-white px-2 text-xs text-zinc-900 focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                    >
                      {MANUAL_OBJECT_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
                      Declared fields ({values.manualFields.length})
                    </span>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={isDiscovering}
                      onClick={() =>
                        onChange((prev) => ({
                          ...prev,
                          manualFields: [
                            ...prev.manualFields,
                            createEmptyManualField(prev.manualFields.length),
                          ],
                        }))
                      }
                    >
                      <Plus className="h-3 w-3" aria-hidden="true" />
                      <span>Add Field</span>
                    </Button>
                  </div>

                  {errors.manualFields && (
                    <p className="text-[11px] text-rose-600">
                      {errors.manualFields}
                    </p>
                  )}

                  <div className="space-y-1.5">
                    {values.manualFields.map((fld, idx) => (
                      <div
                        key={idx}
                        className="grid grid-cols-1 gap-2 rounded border border-zinc-200/80 bg-zinc-50/50 p-2 sm:grid-cols-12 sm:items-center dark:border-zinc-800 dark:bg-zinc-950/40"
                      >
                        <input
                          type="text"
                          aria-label={`Field ${idx + 1} path`}
                          value={fld.fieldPath}
                          onChange={(e) =>
                            updateManualField(idx, {
                              fieldPath: e.target.value,
                            })
                          }
                          placeholder="customer.email"
                          className="h-7 rounded border border-zinc-200 bg-white px-2 font-mono text-[11px] text-zinc-900 sm:col-span-4 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                        />
                        <select
                          aria-label={`Field ${idx + 1} canonical type`}
                          value={fld.canonicalType}
                          onChange={(e) =>
                            updateManualField(idx, {
                              canonicalType: e.target
                                .value as CanonicalFieldType,
                            })
                          }
                          className="h-7 rounded border border-zinc-200 bg-white px-1.5 text-[11px] text-zinc-900 sm:col-span-3 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                        >
                          {CANONICAL_FIELD_TYPES.map((ct) => (
                            <option key={ct} value={ct}>
                              {ct}
                            </option>
                          ))}
                        </select>
                        <input
                          type="text"
                          aria-label={`Field ${idx + 1} native type`}
                          value={fld.nativeType}
                          onChange={(e) =>
                            updateManualField(idx, {
                              nativeType: e.target.value,
                            })
                          }
                          placeholder="native type (optional)"
                          className="h-7 rounded border border-zinc-200 bg-white px-2 font-mono text-[11px] text-zinc-900 sm:col-span-2 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                        />
                        <div className="flex items-center justify-between gap-2 sm:col-span-3">
                          <label className="inline-flex items-center gap-1 text-[11px] text-zinc-600 dark:text-zinc-400">
                            <input
                              type="checkbox"
                              checked={fld.isRequired}
                              onChange={(e) =>
                                updateManualField(idx, {
                                  isRequired: e.target.checked,
                                  nullable: e.target.checked
                                    ? false
                                    : fld.nullable,
                                })
                              }
                            />
                            <span>Required</span>
                          </label>
                          <label className="inline-flex items-center gap-1 text-[11px] text-zinc-600 dark:text-zinc-400">
                            <input
                              type="checkbox"
                              checked={fld.nullable}
                              onChange={(e) =>
                                updateManualField(idx, {
                                  nullable: e.target.checked,
                                })
                              }
                            />
                            <span>Null</span>
                          </label>
                          <button
                            type="button"
                            disabled={values.manualFields.length <= 1}
                            onClick={() =>
                              onChange((prev) => ({
                                ...prev,
                                manualFields: prev.manualFields.filter(
                                  (_, fIdx) => fIdx !== idx,
                                ),
                              }))
                            }
                            aria-label={`Remove field ${idx + 1}`}
                            className="cursor-pointer rounded p-1 text-zinc-400 hover:bg-zinc-200/70 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
