import { createContext, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  MOCK_DASHBOARD_BY_WORKSPACE,
  MOCK_WORKSPACES,
} from '../../data/mockDashboardData'
import type { Workspace, WorkspaceDashboardSnapshot } from '../../types/dashboard'

interface WorkspaceContextValue {
  workspaces: Workspace[]
  activeWorkspace: Workspace
  setWorkspaceId: (id: string) => void
  dashboardData: WorkspaceDashboardSnapshot
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null)

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [workspaceId, setWorkspaceId] = useState<string>(MOCK_WORKSPACES[0].id)

  const value = useMemo<WorkspaceContextValue>(() => {
    const activeWorkspace =
      MOCK_WORKSPACES.find((w) => w.id === workspaceId) ?? MOCK_WORKSPACES[0]
    const dashboardData =
      MOCK_DASHBOARD_BY_WORKSPACE[activeWorkspace.id] ??
      MOCK_DASHBOARD_BY_WORKSPACE[MOCK_WORKSPACES[0].id]

    return {
      workspaces: MOCK_WORKSPACES,
      activeWorkspace,
      setWorkspaceId,
      dashboardData,
    }
  }, [workspaceId])

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext)
  if (!ctx) {
    throw new Error('useWorkspace must be used within WorkspaceProvider')
  }
  return ctx
}
