export type DateRangeKey = '24h' | '7d' | '30d'

export type SyncJobStatus = 'Success' | 'Failed' | 'Running' | 'Pending'

export type ConnectorType =
  | 'PostgreSQL'
  | 'MySQL'
  | 'MongoDB'
  | 'REST API'
  | 'CSV'
  | 'JSON'

export type ConnectionHealth = 'Healthy' | 'Degraded' | 'Inactive'

export type ActivityType =
  | 'sync_completed'
  | 'sync_failed'
  | 'connection_created'
  | 'mapping_updated'
  | 'sync_scheduled'

export type ActivityStatus = 'success' | 'error' | 'info' | 'warning'

export interface Workspace {
  id: string
  name: string
  slug: string
  environment: 'Production' | 'Staging' | 'Sandbox'
}

export interface KpiMetricData {
  id: 'connections' | 'sync_jobs' | 'records_processed' | 'success_rate'
  label: string
  value: string
  secondaryText: string
  trend?: {
    value: string
    direction: 'up' | 'down' | 'neutral'
    isPositive: boolean
  }
  indicator?: {
    label: string
    tone: 'emerald' | 'amber' | 'neutral'
  }
  sparkline: number[]
}

export interface SyncActivityPoint {
  label: string
  fullLabel: string
  successfulSyncs: number
  failedSyncs: number
  recordsProcessed: number
}

export interface SyncJob {
  id: string
  name: string
  schedule: string
  sourceName: string
  sourceType: ConnectorType
  destinationName: string
  destinationType: ConnectorType
  status: SyncJobStatus
  recordsProcessed: number
  durationSeconds: number | null
  lastRunLabel: string
  lastRunTimestamp: number
  errorMessage?: string
}

export interface ConnectionItem {
  id: string
  name: string
  type: ConnectorType
  health: ConnectionHealth
  lastSuccessfulSync: string
  hostHint: string
}

export interface ActivityEvent {
  id: string
  type: ActivityType
  description: string
  actorOrTarget: string
  timestamp: string
  status: ActivityStatus
}

export interface WorkspaceDashboardSnapshot {
  kpisByRange: Record<DateRangeKey, KpiMetricData[]>
  activitySeriesByRange: Record<DateRangeKey, SyncActivityPoint[]>
  recentJobs: SyncJob[]
  connections: ConnectionItem[]
  activityFeed: ActivityEvent[]
}
