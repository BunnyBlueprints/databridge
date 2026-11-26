import { useEffect, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  Activity,
  Cable,
  Check,
  ChevronsUpDown,
  Compass,
  GitBranch,
  LayoutDashboard,
  PanelLeftClose,
  PanelLeftOpen,
  PlaySquare,
  Settings,
  Workflow,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { MOCK_USER_PROFILE } from '../../data/mockDashboardData'
import { Tooltip } from '../ui/Tooltip'
import { useWorkspace } from './WorkspaceContext'

interface NavItemConfig {
  label: string
  to: string
  icon: LucideIcon
  end?: boolean
}

interface NavGroupConfig {
  title: string
  items: NavItemConfig[]
}

const NAV_GROUPS: NavGroupConfig[] = [
  {
    title: 'Overview',
    items: [
      { label: 'Dashboard', to: '/', icon: LayoutDashboard, end: true },
    ],
  },
  {
    title: 'Data Management',
    items: [
      { label: 'Connections', to: '/connections', icon: Cable },
      { label: 'Sync Jobs', to: '/sync-jobs', icon: PlaySquare },
      { label: 'Data Explorer', to: '/data-explorer', icon: Compass },
    ],
  },
  {
    title: 'Development',
    items: [
      { label: 'Mapping Studio', to: '/mapping-studio', icon: Workflow },
      { label: 'Transformations', to: '/transformations', icon: GitBranch },
    ],
  },
  {
    title: 'Monitoring',
    items: [
      { label: 'Activity Logs', to: '/activity-logs', icon: Activity },
    ],
  },
  {
    title: 'Configuration',
    items: [{ label: 'Settings', to: '/settings', icon: Settings }],
  },
]

interface SidebarProps {
  collapsed: boolean
  onToggleCollapse?: () => void
  onNavigate?: () => void
  mobileCloseButton?: boolean
}

function DataBridgeLogo() {
  return (
    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-emerald-600 text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.2)]">
      <svg
        width="16"
        height="16"
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M2.5 5.5C4.5 5.5 5.5 10.5 8 10.5C10.5 10.5 11.5 5.5 13.5 5.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <circle cx="2.5" cy="5.5" r="1.5" fill="currentColor" />
        <circle cx="13.5" cy="5.5" r="1.5" fill="currentColor" />
        <circle cx="8" cy="10.5" r="1.5" fill="currentColor" />
      </svg>
    </div>
  )
}

export function Sidebar({
  collapsed,
  onToggleCollapse,
  onNavigate,
  mobileCloseButton = false,
}: SidebarProps) {
  const { workspaces, activeWorkspace, setWorkspaceId } = useWorkspace()
  const [wsOpen, setWsOpen] = useState(false)
  const wsMenuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!wsOpen) return

    const handleClickOutside = (event: MouseEvent) => {
      if (
        wsMenuRef.current &&
        !wsMenuRef.current.contains(event.target as Node)
      ) {
        setWsOpen(false)
      }
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setWsOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [wsOpen])

  const envDotColor =
    activeWorkspace.environment === 'Production'
      ? 'bg-emerald-500'
      : activeWorkspace.environment === 'Staging'
        ? 'bg-amber-500'
        : 'bg-zinc-400'

  return (
    <div className="flex h-full flex-col bg-white select-none">
      {/* Brand header */}
      <div
        className={`flex h-14 shrink-0 items-center border-b border-zinc-200/80 px-3 ${
          collapsed ? 'justify-center' : 'justify-between'
        }`}
      >
        <NavLink
          to="/"
          onClick={onNavigate}
          className="flex min-w-0 items-center gap-2.5 rounded-md focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none"
        >
          <DataBridgeLogo />
          {!collapsed && (
            <span className="truncate text-sm font-semibold tracking-tight text-zinc-900">
              DataBridge
            </span>
          )}
        </NavLink>

        {mobileCloseButton ? (
          <button
            type="button"
            onClick={onNavigate}
            aria-label="Close navigation menu"
            className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        ) : (
          onToggleCollapse &&
          !collapsed && (
            <button
              type="button"
              onClick={onToggleCollapse}
              aria-label="Collapse sidebar"
              title="Collapse sidebar"
              className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none"
            >
              <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
            </button>
          )
        )}
      </div>

      {/* Expand button when collapsed */}
      {collapsed && onToggleCollapse && (
        <div className="flex justify-center border-b border-zinc-100 py-2">
          <Tooltip label="Expand sidebar">
            <div className="flex w-full justify-center">
              <button
                type="button"
                onClick={onToggleCollapse}
                aria-label="Expand sidebar"
                className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none"
              >
                <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </Tooltip>
        </div>
      )}

      {/* Workspace selector */}
      <div
        ref={wsMenuRef}
        className="relative border-b border-zinc-200/80 px-2.5 py-2.5"
      >
        <Tooltip
          label={`${activeWorkspace.name} (${activeWorkspace.environment})`}
          enabled={collapsed}
        >
          <button
            type="button"
            onClick={() => setWsOpen((prev) => !prev)}
            aria-haspopup="listbox"
            aria-expanded={wsOpen}
            aria-label="Select workspace"
            className={`flex w-full cursor-pointer items-center rounded-md border border-zinc-200/90 bg-zinc-50/70 text-left transition-colors hover:border-zinc-300 hover:bg-zinc-100/70 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none ${
              collapsed ? 'justify-center p-2' : 'justify-between px-2.5 py-1.5'
            }`}
          >
            <div className="flex min-w-0 items-center gap-2">
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${envDotColor}`}
                aria-hidden="true"
              />
              {!collapsed && (
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium text-zinc-900">
                    {activeWorkspace.name}
                  </div>
                  <div className="truncate text-[11px] text-zinc-500">
                    {activeWorkspace.environment}
                  </div>
                </div>
              )}
            </div>
            {!collapsed && (
              <ChevronsUpDown
                className="h-3.5 w-3.5 shrink-0 text-zinc-400"
                aria-hidden="true"
              />
            )}
          </button>
        </Tooltip>

        {wsOpen && (
          <div
            role="listbox"
            aria-label="Workspaces"
            className={`absolute z-50 mt-1.5 rounded-lg border border-zinc-200 bg-white p-1 shadow-lg ${
              collapsed ? 'left-full top-0 ml-2 w-56' : 'right-2.5 left-2.5'
            }`}
          >
            <div className="px-2 py-1 text-[10px] font-semibold tracking-wider text-zinc-400 uppercase">
              Workspaces
            </div>
            {workspaces.map((ws) => {
              const selected = ws.id === activeWorkspace.id
              const dot =
                ws.environment === 'Production'
                  ? 'bg-emerald-500'
                  : ws.environment === 'Staging'
                    ? 'bg-amber-500'
                    : 'bg-zinc-400'
              return (
                <button
                  key={ws.id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    setWorkspaceId(ws.id)
                    setWsOpen(false)
                  }}
                  className={`flex w-full cursor-pointer items-center justify-between rounded-md px-2 py-1.5 text-left text-xs transition-colors ${
                    selected
                      ? 'bg-emerald-50/80 text-emerald-900'
                      : 'text-zinc-700 hover:bg-zinc-100'
                  }`}
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`}
                      aria-hidden="true"
                    />
                    <div className="min-w-0">
                      <div className="truncate font-medium">{ws.name}</div>
                      <div className="truncate text-[11px] text-zinc-500">
                        {ws.environment} · {ws.slug}
                      </div>
                    </div>
                  </div>
                  {selected && (
                    <Check
                      className="h-3.5 w-3.5 shrink-0 text-emerald-600"
                      aria-hidden="true"
                    />
                  )}
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* Navigation groups */}
      <nav
        aria-label="Primary navigation"
        className="flex-1 space-y-4 overflow-y-auto px-2.5 py-3"
      >
        {NAV_GROUPS.map((group, groupIdx) => (
          <div
            key={group.title}
            className={
              groupIdx > 0 ? 'border-t border-zinc-100 pt-3' : undefined
            }
          >
            {!collapsed && (
              <div className="mb-1.5 px-2 text-[11px] font-medium tracking-wider text-zinc-400 uppercase">
                {group.title}
              </div>
            )}
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const Icon = item.icon
                return (
                  <li key={item.to}>
                    <Tooltip label={item.label} enabled={collapsed}>
                      <NavLink
                        to={item.to}
                        end={item.end}
                        onClick={onNavigate}
                        className={({ isActive }) =>
                          `flex w-full items-center rounded-md text-[13px] font-medium transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:outline-none ${
                            collapsed
                              ? 'justify-center p-2'
                              : 'gap-2.5 px-2.5 py-1.5'
                          } ${
                            isActive
                              ? 'bg-emerald-50 text-emerald-700'
                              : 'text-zinc-600 hover:bg-zinc-100/80 hover:text-zinc-900'
                          }`
                        }
                      >
                        {({ isActive }) => (
                          <>
                            <Icon
                              className={`h-4 w-4 shrink-0 ${
                                isActive ? 'text-emerald-600' : 'text-zinc-400'
                              }`}
                              aria-hidden="true"
                            />
                            {!collapsed && (
                              <span className="truncate">{item.label}</span>
                            )}
                          </>
                        )}
                      </NavLink>
                    </Tooltip>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* Bottom user profile */}
      <div className="border-t border-zinc-200/80 p-2.5">
        <Tooltip
          label={`${MOCK_USER_PROFILE.name} · ${MOCK_USER_PROFILE.role}`}
          enabled={collapsed}
        >
          <div
            className={`flex w-full items-center rounded-md px-2 py-1.5 ${
              collapsed ? 'justify-center' : 'gap-2.5'
            }`}
          >
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-emerald-200 bg-emerald-50 text-xs font-semibold text-emerald-700">
              {MOCK_USER_PROFILE.initials}
            </div>
            {!collapsed && (
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium text-zinc-900">
                  {MOCK_USER_PROFILE.name}
                </div>
                <div className="truncate text-[11px] text-zinc-500">
                  {MOCK_USER_PROFILE.role}
                </div>
              </div>
            )}
          </div>
        </Tooltip>
      </div>
    </div>
  )
}
