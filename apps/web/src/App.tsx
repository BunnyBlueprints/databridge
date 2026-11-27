import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import {
  Activity,
  GitBranch,
  PlaySquare,
  PlusSquare,
  Settings,
} from 'lucide-react'
import { AppShell } from './components/layout/AppShell'
import { WorkspaceProvider } from './components/layout/WorkspaceContext'
import { ConnectionsPage } from './pages/ConnectionsPage'
import { DashboardPage } from './pages/DashboardPage'
import { DataExplorerPage } from './pages/DataExplorerPage'
import { MappingStudioPage } from './pages/MappingStudioPage'
import { PlaceholderPage } from './pages/PlaceholderPage'
import { SyncJobDetailPage } from './pages/SyncJobDetailPage'

function App() {
  return (
    <BrowserRouter>
      <WorkspaceProvider>
        <AppShell>
          <Routes>
            <Route path="/" element={<DashboardPage />} />

            <Route path="/connections" element={<ConnectionsPage />} />

            <Route
              path="/sync-jobs"
              element={
                <PlaceholderPage
                  title="Sync Jobs"
                  description="Configure schedules, incremental cursors, and execution policies for your pipelines."
                  icon={PlaySquare}
                  plannedCapabilities={[
                    'Cron and interval scheduling with backfill windows',
                    'Idempotent checkpoint and retry policy configuration',
                    'Real-time worker run history and batch metrics',
                  ]}
                />
              }
            />

            <Route
              path="/sync-jobs/new"
              element={
                <PlaceholderPage
                  title="Create Sync Job"
                  description="Set up a new source-to-destination synchronization pipeline."
                  icon={PlusSquare}
                  plannedCapabilities={[
                    'Select configured source and destination connectors',
                    'Choose full-refresh or incremental CDC sync modes',
                    'Attach schema mappings and validation rules before activation',
                  ]}
                />
              }
            />

            <Route path="/sync-jobs/:jobId" element={<SyncJobDetailPage />} />

            <Route path="/data-explorer" element={<DataExplorerPage />} />

            <Route path="/mapping-studio" element={<MappingStudioPage />} />

            <Route
              path="/transformations"
              element={
                <PlaceholderPage
                  title="Transformations"
                  description="Author reusable normalization, filtering, and enrichment transforms."
                  icon={GitBranch}
                  plannedCapabilities={[
                    'Versioned field transformation pipelines',
                    'Deterministic unit testing with sample input payloads',
                    'Reusable expression presets for timestamps, PII masking, and JSON flattening',
                  ]}
                />
              }
            />

            <Route
              path="/activity-logs"
              element={
                <PlaceholderPage
                  title="Activity Logs"
                  description="Audit worker execution logs, schema changes, and workspace events."
                  icon={Activity}
                  plannedCapabilities={[
                    'Filterable audit trail by job, connector, severity, and time window',
                    'Structured error traces for failed batches',
                    'Exportable compliance logs',
                  ]}
                />
              }
            />

            <Route
              path="/settings"
              element={
                <PlaceholderPage
                  title="Settings"
                  description="Configure workspace defaults, alerting thresholds, and team access."
                  icon={Settings}
                  plannedCapabilities={[
                    'Workspace environment metadata and retention windows',
                    'Failure alert channels (webhooks, Slack, email)',
                    'API key management and role-based permissions',
                  ]}
                />
              }
            />

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AppShell>
      </WorkspaceProvider>
    </BrowserRouter>
  )
}

export default App
