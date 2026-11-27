import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { DeleteMappingDialog } from '../components/mapping-studio/DeleteMappingDialog.tsx'
import { MappingEditorView } from '../components/mapping-studio/MappingEditorView.tsx'
import {
  MappingListView,
  type MappingStatusFilter,
} from '../components/mapping-studio/MappingListView.tsx'
import { Button } from '../components/ui/Button.tsx'
import {
  ConnectionApiClientError,
  listConnections,
} from '../services/connectionService.ts'
import {
  deleteMapping,
  getMapping,
  listMappings,
  MappingApiClientError,
} from '../services/mappingService.ts'
import type { Connection } from '../types/connection.ts'
import type { MappingRecord } from '../types/mapping.ts'

const MAPPINGS_PAGE_LIMIT = 20

export function MappingStudioPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const urlMappingId = searchParams.get('mappingId')

  const [connections, setConnections] = useState<Connection[]>([])
  const [mappings, setMappings] = useState<MappingRecord[]>([])
  const [totalMappings, setTotalMappings] = useState(0)
  const [offset, setOffset] = useState(0)

  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<MappingStatusFilter>('All')
  const [sourceConnFilter, setSourceConnFilter] = useState('')
  const [destConnFilter, setDestConnFilter] = useState('')

  const [isLoadingList, setIsLoadingList] = useState(true)
  const [isRefreshingList, setIsRefreshingList] = useState(false)
  const [listError, setListError] = useState<string | null>(null)

  const [viewMode, setViewMode] = useState<'list' | 'editor'>(() =>
    urlMappingId ? 'editor' : 'list',
  )
  const [editorSessionKey, setEditorSessionKey] = useState<string>(
    () => urlMappingId ?? 'list',
  )
  const [activeMapping, setActiveMapping] = useState<MappingRecord | null>(null)
  const [isLoadingDetail, setIsLoadingDetail] = useState<boolean>(() =>
    Boolean(urlMappingId),
  )
  const [detailError, setDetailError] = useState<string | null>(null)

  const [deletingMapping, setDeletingMapping] = useState<MappingRecord | null>(
    null,
  )
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const connectionsAbortRef = useRef<AbortController | null>(null)
  const listAbortRef = useRef<AbortController | null>(null)
  const detailAbortRef = useRef<AbortController | null>(null)
  const hasLoadedListOnceRef = useRef(false)
  const closingUrlIdRef = useRef<string | null>(null)

  useEffect(() => {
    const timer = globalThis.setTimeout(() => {
      setDebouncedSearch(searchQuery.trim())
      setOffset(0)
    }, 180)
    return () => globalThis.clearTimeout(timer)
  }, [searchQuery])

  const loadConnectionsList = useCallback(async () => {
    connectionsAbortRef.current?.abort()
    const controller = new AbortController()
    connectionsAbortRef.current = controller
    try {
      const res = await listConnections(
        { limit: 200 },
        { signal: controller.signal },
      )
      if (!controller.signal.aborted) {
        setConnections(res.items)
      }
    } catch (err) {
      if (err instanceof ConnectionApiClientError && err.isAborted) return
    }
  }, [])

  const loadMappingsList = useCallback(
    async (opts?: { isManualRefresh?: boolean }) => {
      listAbortRef.current?.abort()
      const controller = new AbortController()
      listAbortRef.current = controller

      if (!hasLoadedListOnceRef.current && !opts?.isManualRefresh) {
        setIsLoadingList(true)
      } else {
        setIsRefreshingList(true)
      }
      setListError(null)

      try {
        const res = await listMappings(
          {
            ...(debouncedSearch ? { q: debouncedSearch } : {}),
            ...(statusFilter !== 'All' ? { status: statusFilter } : {}),
            ...(sourceConnFilter ? { sourceConnectionId: sourceConnFilter } : {}),
            ...(destConnFilter
              ? { destinationConnectionId: destConnFilter }
              : {}),
            limit: MAPPINGS_PAGE_LIMIT,
            offset,
          },
          { signal: controller.signal },
        )
        if (controller.signal.aborted) return
        hasLoadedListOnceRef.current = true
        setMappings(res.items)
        setTotalMappings(res.total)
      } catch (err) {
        if (err instanceof MappingApiClientError && err.isAborted) return
        setListError(
          err instanceof Error
            ? err.message
            : 'Unable to load saved mappings.',
        )
      } finally {
        if (!controller.signal.aborted) {
          setIsLoadingList(false)
          setIsRefreshingList(false)
        }
      }
    },
    [debouncedSearch, statusFilter, sourceConnFilter, destConnFilter, offset],
  )

  const openEditorForId = useCallback(async (mappingId: string) => {
    detailAbortRef.current?.abort()
    const controller = new AbortController()
    detailAbortRef.current = controller

    setViewMode('editor')
    setIsLoadingDetail(true)
    setDetailError(null)
    try {
      const record = await getMapping(mappingId, { signal: controller.signal })
      if (controller.signal.aborted) return
      setActiveMapping(record)
      setEditorSessionKey(record.id)
    } catch (err) {
      if (err instanceof MappingApiClientError && err.isAborted) return
      setDetailError(
        err instanceof Error
          ? err.message
          : 'Unable to load the selected mapping.',
      )
    } finally {
      if (!controller.signal.aborted) {
        setIsLoadingDetail(false)
      }
    }
  }, [])

  useEffect(() => {
    void loadConnectionsList()
    return () => {
      connectionsAbortRef.current?.abort()
      listAbortRef.current?.abort()
      detailAbortRef.current?.abort()
    }
  }, [loadConnectionsList])

  useEffect(() => {
    void loadMappingsList()
  }, [loadMappingsList])

  useEffect(() => {
    if (!urlMappingId) {
      closingUrlIdRef.current = null
      return
    }
    if (closingUrlIdRef.current === urlMappingId) {
      return
    }
    if (activeMapping?.id === urlMappingId) {
      return
    }
    void openEditorForId(urlMappingId)
  }, [urlMappingId, activeMapping?.id, openEditorForId])

  const handleOpenCreate = () => {
    detailAbortRef.current?.abort()
    closingUrlIdRef.current = urlMappingId
    setDetailError(null)
    setIsLoadingDetail(false)
    setActiveMapping(null)
    setEditorSessionKey(`new-${Date.now()}`)
    setViewMode('editor')
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('mappingId')
      return next
    })
  }

  const handleOpenExisting = (mapping: MappingRecord) => {
    closingUrlIdRef.current = null
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('mappingId', mapping.id)
      return next
    })
  }

  const handleBackToList = () => {
    detailAbortRef.current?.abort()
    closingUrlIdRef.current = urlMappingId
    setViewMode('list')
    setActiveMapping(null)
    setDetailError(null)
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('mappingId')
      return next
    })
    void loadMappingsList({ isManualRefresh: true })
  }

  const handleMappingSaved = (persisted: MappingRecord) => {
    closingUrlIdRef.current = null
    setActiveMapping(persisted)
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('mappingId', persisted.id)
      return next
    })
    void loadMappingsList({ isManualRefresh: true })
  }

  const handleConfirmDelete = async (mapping: MappingRecord) => {
    setIsDeleting(true)
    setDeleteError(null)
    try {
      await deleteMapping(mapping.id)
      setDeletingMapping(null)
      if (mappings.length === 1 && offset > 0) {
        setOffset(Math.max(0, offset - MAPPINGS_PAGE_LIMIT))
      } else {
        void loadMappingsList({ isManualRefresh: true })
      }
    } catch (err) {
      setDeleteError(
        err instanceof Error ? err.message : 'Unable to delete mapping.',
      )
    } finally {
      setIsDeleting(false)
    }
  }

  if (viewMode === 'editor') {
    if (isLoadingDetail) {
      return (
        <div className="flex flex-col items-center justify-center py-20 text-xs text-zinc-500">
          <Loader2
            className="mb-2.5 h-6 w-6 animate-spin text-emerald-600"
            aria-hidden="true"
          />
          <span>Loading mapping configuration and ordered rules…</span>
        </div>
      )
    }

    if (detailError) {
      return (
        <div
          role="alert"
          className="mx-auto max-w-lg rounded-lg border border-rose-200 bg-rose-50/70 p-6 text-center text-xs text-rose-900 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-100"
        >
          <AlertCircle
            className="mx-auto h-7 w-7 text-rose-500"
            aria-hidden="true"
          />
          <p className="mt-2 text-sm font-semibold">Unable to open mapping</p>
          <p className="mt-1 opacity-90">{detailError}</p>
          <div className="mt-4 flex justify-center gap-2">
            <Button variant="secondary" size="sm" onClick={handleBackToList}>
              Back to Mappings
            </Button>
            {urlMappingId && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => void openEditorForId(urlMappingId)}
              >
                Retry
              </Button>
            )}
          </div>
        </div>
      )
    }

    return (
      <MappingEditorView
        key={editorSessionKey}
        connections={connections}
        initialMapping={activeMapping}
        onBackToList={handleBackToList}
        onMappingSaved={handleMappingSaved}
      />
    )
  }

  return (
    <>
      <MappingListView
        mappings={mappings}
        total={totalMappings}
        limit={MAPPINGS_PAGE_LIMIT}
        offset={offset}
        connections={connections}
        searchQuery={searchQuery}
        statusFilter={statusFilter}
        sourceConnectionFilter={sourceConnFilter}
        destinationConnectionFilter={destConnFilter}
        isLoading={isLoadingList}
        isRefreshing={isRefreshingList}
        error={listError}
        onSearchChange={setSearchQuery}
        onStatusFilterChange={(nextStatus) => {
          setStatusFilter(nextStatus)
          setOffset(0)
        }}
        onSourceConnectionFilterChange={(connId) => {
          setSourceConnFilter(connId)
          setOffset(0)
        }}
        onDestinationConnectionFilterChange={(connId) => {
          setDestConnFilter(connId)
          setOffset(0)
        }}
        onClearFilters={() => {
          setSearchQuery('')
          setDebouncedSearch('')
          setStatusFilter('All')
          setSourceConnFilter('')
          setDestConnFilter('')
          setOffset(0)
        }}
        onPageChange={setOffset}
        onRefresh={() => {
          void loadConnectionsList()
          void loadMappingsList({ isManualRefresh: true })
        }}
        onCreateNew={handleOpenCreate}
        onOpenMapping={handleOpenExisting}
        onDeleteRequest={(mapping) => {
          setDeleteError(null)
          setDeletingMapping(mapping)
        }}
      />

      <DeleteMappingDialog
        mapping={deletingMapping}
        isDeleting={isDeleting}
        errorMessage={deleteError}
        onCancel={() => {
          if (!isDeleting) {
            setDeleteError(null)
            setDeletingMapping(null)
          }
        }}
        onConfirmDelete={(mapping) => void handleConfirmDelete(mapping)}
      />
    </>
  )
}
